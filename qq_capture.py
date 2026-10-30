"""Capture QQ chat area and OCR into 对方/自己 dialogue (dark-mode aware)."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageStat

from qq_send import find_qq_window
from wechat_capture import (
    ROOT,
    _capture_window,
    _clean_ocr_text,
    _is_noise_line,
    _is_ui_noise_line,
    format_dialogue,
    parse_dialogue,
    _run_ocr,
)

CONFIG_PATH = ROOT / "config.json"

DEFAULT_QQ_CROP = {
    "left": 0.30,
    "top": 0.05,
    "right": 0.995,
    "bottom": 0.82,
    "recent_from": 0.72,
}

_QQ_UI_NOISE = re.compile(
    r"(语音通话|视频通话|屏幕共享|创建|搜索|消息|联系人|动态|频道|"
    r"文件管理|收藏|相册|发送|截图|表情|截屏|更多|"
    r"QQ会员|超级会员|好友动态|群助手|腾讯|企点)"
)
# timestamps often OCR'd with spaces: "13 ： 29"
_TIME_ONLY = re.compile(
    r"^(\d{1,2}\s*[：:]\s*\d{2}(?:\s*[：:]\s*\d{2})?|"
    r"昨天|今天|星期[一二三四五六日天])$"
)


def _load_qq_crop() -> dict:
    crop = dict(DEFAULT_QQ_CROP)
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        user = cfg.get("qq_capture_crop") or {}
        for key in DEFAULT_QQ_CROP:
            if key in user:
                crop[key] = float(user[key])
    except Exception:
        pass
    return crop


def _col_brightness(img: Image.Image, x: int, y0: int, y1: int) -> float:
    total = 0.0
    n = 0
    for y in range(y0, y1, 4):
        p = img.getpixel((x, y))
        total += (p[0] + p[1] + p[2]) / 3.0
        n += 1
    return total / max(n, 1)


def _detect_qq_chat_left(img: Image.Image) -> int | None:
    w, h = img.size
    if w < 400:
        return None
    y0, y1 = int(h * 0.25), int(h * 0.7)
    if y1 <= y0:
        return None

    best_x = None
    best_score = 0.0
    prev = _col_brightness(img, int(w * 0.12), y0, y1)
    for x in range(int(w * 0.14), int(w * 0.55), 3):
        cur = _col_brightness(img, x, y0, y1)
        score = abs(cur - prev)
        if score > best_score and x > int(w * 0.18):
            best_score = score
            best_x = x
        prev = cur

    if best_x is None or best_score < 4.0:
        return int(w * 0.32)
    return min(w - 120, best_x + 6)


def _is_dark_theme(img: Image.Image) -> bool:
    sample = img.resize((64, 64), Image.Resampling.BILINEAR)
    mean = sum(ImageStat.Stat(sample.convert("RGB")).mean[:3]) / 3.0
    return mean < 95


def _enhance_qq_sharp(img: Image.Image) -> Image.Image:
    """Light sharpen pass to recover thin glyphs missed by raw OCR."""
    work = img.convert("RGB")
    work = ImageEnhance.Sharpness(work).enhance(2.6)
    work = work.filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=2))
    return work


def _normalize_qq_text(text: str) -> str:
    t = (text or "").strip()
    # collapse spaces between CJK / latin+CJK / around colon
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9])", "", t)
    t = re.sub(r"\s*([：:])\s*", r"\1", t)
    t = re.sub(r"(?<=\d)\s+(?=\d)", "", t)
    # trailing misread quotes → ellipsis common in QQ
    t = re.sub(r"[“\"”]+$", "..", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    return t


def _is_qq_noise_line(text: str, *, height: float = 0, y: float = 0) -> bool:
    t = _normalize_qq_text(text)
    if not t:
        return True
    if _is_noise_line(t) or _is_ui_noise_line(t):
        return True
    if _TIME_ONLY.match(t):
        return True
    if _QQ_UI_NOISE.search(t):
        return True
    # icon OCR crumbs
    if re.fullmatch(r"[0Oo回。．·•…]{1,3}", t):
        return True
    if re.fullmatch(r"[^\w\u4e00-\u9fff]{1,4}", t):
        return True
    if re.fullmatch(r"[A-Za-z]{1,2}", t):
        return True
    # top header band (contact title)
    if height > 0 and y < height * 0.07 and len(t) <= 20:
        # short top-left titles / status icons
        if not t.startswith("自动"):
            return True
    return False


def _line_key(text: str) -> str:
    return re.sub(r"\s+", "", _normalize_qq_text(text))


def _ocr_pass(img: Image.Image, *, scale: int, tag: str) -> dict:
    big = img.resize(
        (max(1, img.size[0] * scale), max(1, img.size[1] * scale)),
        Image.Resampling.LANCZOS,
    )
    with tempfile.TemporaryDirectory(prefix=f"qqocr_{tag}_") as tmp:
        path = Path(tmp) / f"{tag}.png"
        big.save(path)
        try:
            big.save(ROOT / f"_debug_qq_ocr_{tag}.png")
        except Exception:
            pass
        return _run_ocr(path), float(scale)


def _rows_from_ocr(ocr: dict, scale: float, *, height: float) -> list[dict]:
    rows: list[dict] = []
    for item in ocr.get("lines") or []:
        raw = _clean_ocr_text(item.get("text") or "")
        text = _normalize_qq_text(raw)
        y = float(item.get("y") or 0) / scale
        x = float(item.get("x") or 0) / scale
        w = float(item.get("w") or 0) / scale
        h = float(item.get("h") or 0) / scale
        if _is_qq_noise_line(text, height=height, y=y):
            continue
        rows.append({"text": text, "x": x, "y": y, "w": w, "h": h})
    rows.sort(key=lambda r: (r["y"], r["x"]))
    return rows


def _merge_rows(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: list[dict] = []
    used = set()

    def near(p: dict, q: dict) -> bool:
        return abs(p["y"] - q["y"]) < max(16.0, p.get("h", 14) * 0.9) and abs(
            p["x"] - q["x"]
        ) < 100

    for p in primary:
        best_i = -1
        best = None
        for i, q in enumerate(secondary):
            if i in used:
                continue
            if near(p, q):
                best_i = i
                best = q
                break
        if best is None:
            merged.append(p)
            continue
        used.add(best_i)
        # prefer more CJK / longer content
        def score(r: dict) -> tuple:
            t = r["text"]
            cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
            return (cjk, len(_line_key(t)))

        merged.append(best if score(best) > score(p) else p)

    for i, q in enumerate(secondary):
        if i not in used:
            # only add if not duplicate text near existing
            dup = False
            for m in merged:
                if _line_key(m["text"]) == _line_key(q["text"]) and abs(m["y"] - q["y"]) < 20:
                    dup = True
                    break
                if near(m, q) and _line_key(q["text"]) in _line_key(m["text"]):
                    dup = True
                    break
            if not dup:
                merged.append(q)

    merged.sort(key=lambda r: (r["y"], r["x"]))
    return merged


def _fix_header_name(rows: list[dict], title_hint: str, height: float) -> list[dict]:
    hint = (title_hint or "").strip()
    if not hint or hint in {"QQ", "QQNT"}:
        # drop first top-left short line (usually contact name)
        if rows and rows[0]["y"] < height * 0.1 and rows[0]["x"] < 200:
            return rows[1:]
        return rows
    out = []
    for r in rows:
        key = _line_key(r["text"])
        hint_key = _line_key(hint)
        if r["y"] < height * 0.12 and (
            key == hint_key or hint_key in key or key in hint_key
        ):
            continue
        out.append(r)
    return out


def _ocr_qq_region(img: Image.Image, *, title_hint: str = "") -> str:
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")
    try:
        img.save(ROOT / "_debug_qq_chat.png")
    except Exception:
        pass

    h = float(img.size[1])
    # Primary: raw color upscale — best for QQ dark theme (invert hurts accuracy)
    scale_main = 3
    ocr1, s1 = _ocr_pass(img, scale=scale_main, tag="raw")
    rows1 = _rows_from_ocr(ocr1, s1, height=h)

    # Secondary: sharpen — recovers thin glyphs like「聊天」
    ocr2, s2 = _ocr_pass(_enhance_qq_sharp(img), scale=scale_main, tag="sharp")
    rows2 = _rows_from_ocr(ocr2, s2, height=h)

    rows = _merge_rows(rows1, rows2)
    rows = _fix_header_name(rows, title_hint, h)

    fake_ocr = {
        "ok": True,
        "width": float(img.size[0]),
        "height": h,
        "text": "\n".join(r["text"] for r in rows),
        "lines": rows,
    }
    turns = parse_dialogue(fake_ocr, image=img, scale=1.0)
    cleaned = []
    for t in turns:
        parts = []
        for p in (t.get("text") or "").splitlines():
            p2 = _normalize_qq_text(p)
            if p2 and not _is_qq_noise_line(p2):
                parts.append(p2)
        if parts:
            cleaned.append({"role": t["role"], "text": "\n".join(parts)})
    text = format_dialogue(cleaned)
    if text:
        return text
    # last resort: plain lines
    if rows:
        return "\n".join(f"对方：{r['text']}" for r in rows)
    raise RuntimeError("QQ 聊天文字识别为空，请把窗口最大化或调亮后再试")


def _crop_qq_chat(img: Image.Image, crop: dict | None = None) -> Image.Image:
    crop = crop or _load_qq_crop()
    w, h = img.size
    detected = _detect_qq_chat_left(img)
    left = int(w * crop["left"])
    if detected is not None:
        left = max(left, detected)
    top = int(h * crop["top"])
    # skip title bar inside chat pane (~48-56px)
    top = max(top, int(h * 0.05) + 48)
    right = int(w * crop["right"])
    bottom = int(h * crop["bottom"])
    # drop input toolbar
    bottom = min(bottom, h - 64)
    if right - left < 100 or bottom - top < 100:
        return img
    return img.crop((left, top, right, bottom))


def capture_qq_dialogue(title_hint: str = "QQ", *, history: bool = True) -> str:
    del history
    hwnd = find_qq_window(title_hint)
    if not hwnd:
        raise RuntimeError(
            "找不到 QQ 窗口。请先打开桌面版 QQ 并进入聊天，"
            "或在 config.json 里设置 qq_window_title 为当前聊天标题。"
        )

    img = _capture_window(hwnd)
    try:
        img.save(ROOT / "_debug_qq_full.png")
    except Exception:
        pass

    # Prefer actual window title for header filtering
    try:
        import win32gui

        win_title = win32gui.GetWindowText(hwnd) or title_hint
    except Exception:
        win_title = title_hint

    region = _crop_qq_chat(img)
    return _ocr_qq_region(region, title_hint=win_title)
