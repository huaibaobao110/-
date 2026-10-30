"""
微信日常聊天半自动回复助手

- 按长辈/平辈/晚辈切换主题色与动画
- 支持多个聊天对象，各自保存回复偏好
- 识别后自动生成候选
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from clipboard_util import get_text, set_text
import contacts as contacts_store
from icon_util import copy_icon
from reply_ai import MAX_MODES, RELATION_OPTIONS, default_modes_for, generate_replies, style_labels_for
from qq_capture import capture_qq_dialogue
from qq_send import find_qq_window, paste_and_send as qq_paste_and_send
from wechat_capture import (
    capture_latest_incoming,
    flip_dialogue_roles,
    recognize_from_clipboard_image,
    recognize_from_path,
    snip_and_recognize,
)
from wechat_send import find_wechat_window, paste_and_send as wx_paste_and_send

PLATFORMS = ("微信", "QQ")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

# Distinct palettes per relation (no purple / cream-terracotta)
THEMES = {
    "长辈": {
        "bg": "#E7EEF6",
        "card": "#FFFFFF",
        "ink": "#152033",
        "muted": "#5C6B7A",
        "line": "#CDD8E6",
        "accent": "#1D4E89",
        "accent_hover": "#163A66",
        "soft": "#D9E6F5",
        "chip": "#B7C9DE",
    },
    "平辈": {
        "bg": "#E6F3F1",
        "card": "#FFFFFF",
        "ink": "#14212B",
        "muted": "#5B6B78",
        "line": "#C9DED9",
        "accent": "#0F766E",
        "accent_hover": "#0D9488",
        "soft": "#CCFBF1",
        "chip": "#99D5CB",
    },
    "晚辈": {
        "bg": "#EEF6E8",
        "card": "#FFFFFF",
        "ink": "#1A2A14",
        "muted": "#5F7058",
        "line": "#D2E0C8",
        "accent": "#3F6212",
        "accent_hover": "#4D7C0F",
        "soft": "#E8F5D5",
        "chip": "#B5D48A",
    },
}


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp_hex(a: str, b: str, t: float) -> str:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        if EXAMPLE_PATH.exists():
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileNotFoundError("缺少 config.json")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class ContactDialog(ctk.CTkToplevel):
    def __init__(self, master, *, title: str, initial: dict | None = None):
        super().__init__(master)
        self.title(title)
        self.geometry("520x720")
        self.minsize(480, 600)
        self.transient(master)
        self.grab_set()
        self.result: dict | None = None

        initial = initial or {}
        relation = initial.get("relation", "平辈")
        modes = initial.get("modes") or []
        defaults = default_modes_for(relation)
        if len(modes) < 3:
            modes = defaults
        # pad to default count (平辈 4)
        while len(modes) < len(defaults):
            modes.append(dict(defaults[len(modes)]))

        pad = ctk.CTkScrollableFrame(self, fg_color="transparent")
        pad.pack(fill="both", expand=True, padx=14, pady=12)

        ctk.CTkLabel(pad, text="对象名称", anchor="w").pack(fill="x")
        self.name_var = tk.StringVar(value=initial.get("name", ""))
        ctk.CTkEntry(pad, textvariable=self.name_var, height=34).pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(pad, text="身份类型", anchor="w").pack(fill="x")
        self.relation_var = tk.StringVar(value=relation)
        self.relation_seg = ctk.CTkSegmentedButton(
            pad,
            values=list(RELATION_OPTIONS),
            variable=self.relation_var,
            command=self._on_relation,
        )
        self.relation_seg.pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(pad, text="回复前缀（可空，如 自动：）", anchor="w").pack(fill="x")
        self.prefix_var = tk.StringVar(value=initial.get("reply_prefix", "自动："))
        ctk.CTkEntry(pad, textvariable=self.prefix_var, height=34).pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(
            pad,
            text="总人设 / 专属话术（每条候选都会按这个改）",
            anchor="w",
        ).pack(fill="x")
        self.style_box = ctk.CTkTextbox(pad, height=72)
        self.style_box.pack(fill="x", pady=(4, 10))
        self.style_box.insert("0.0", initial.get("style", ""))

        ctk.CTkLabel(
            pad,
            text="说话方式（名称 + 如何改写话术；平辈含阴阳怪气）",
            anchor="w",
        ).pack(fill="x", pady=(4, 4))

        self.mode_name_vars: list[tk.StringVar] = []
        self.mode_guide_boxes: list[ctk.CTkTextbox] = []
        mode_count = min(MAX_MODES, max(len(modes), len(defaults)))
        for i in range(mode_count):
            m = modes[i] if i < len(modes) else {"name": f"方式{i+1}", "guide": ""}
            block = ctk.CTkFrame(pad, fg_color=("gray90", "gray20"), corner_radius=10)
            block.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(block, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=8)
            ctk.CTkLabel(inner, text=f"方式 {i+1} 名称", anchor="w").pack(fill="x")
            nv = tk.StringVar(value=m.get("name", ""))
            self.mode_name_vars.append(nv)
            ctk.CTkEntry(inner, textvariable=nv, height=30).pack(fill="x", pady=(2, 6))
            ctk.CTkLabel(inner, text="该方式如何改写话术", anchor="w").pack(fill="x")
            gb = ctk.CTkTextbox(inner, height=54)
            gb.pack(fill="x", pady=(2, 0))
            gb.insert("0.0", m.get("guide", ""))
            self.mode_guide_boxes.append(gb)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkButton(row, text="取消", width=90, command=self.destroy).pack(side="right")
        ctk.CTkButton(row, text="保存", width=90, command=self._ok).pack(
            side="right", padx=(0, 8)
        )
        ctk.CTkButton(
            row,
            text="恢复默认方式",
            width=110,
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color="#0F172A",
            command=self._reset_modes,
        ).pack(side="left")

        self.after(50, self._center)

    def _on_relation(self, _value: str | None = None) -> None:
        # only prefill if guides still look like previous defaults empty-ish
        pass

    def _reset_modes(self) -> None:
        defaults = default_modes_for(self.relation_var.get())
        for i in range(len(self.mode_name_vars)):
            if i < len(defaults):
                self.mode_name_vars[i].set(defaults[i]["name"])
                self.mode_guide_boxes[i].delete("0.0", "end")
                self.mode_guide_boxes[i].insert("0.0", defaults[i]["guide"])
            else:
                self.mode_name_vars[i].set("")
                self.mode_guide_boxes[i].delete("0.0", "end")

    def _center(self) -> None:
        self.update_idletasks()
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _ok(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showinfo("提示", "请填写对象名称", parent=self)
            return
        modes = []
        for i in range(len(self.mode_name_vars)):
            name = self.mode_name_vars[i].get().strip()
            guide = self.mode_guide_boxes[i].get("0.0", "end").strip()
            if not name and not guide:
                continue
            modes.append({"name": name or f"方式{i+1}", "guide": guide})
        if len(modes) < 3:
            messagebox.showinfo("提示", "请至少填写 3 种说话方式", parent=self)
            return
        self.result = {
            "name": name,
            "relation": self.relation_var.get(),
            "style": self.style_box.get("0.0", "end").strip(),
            "reply_prefix": self.prefix_var.get(),
            "modes": modes,
        }
        self.destroy()


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("green")

        self.title("聊天回复助手 · 微信/QQ")
        self.geometry("640x820")
        self.minsize(560, 720)

        self.config_data = load_config()
        self.active_contact = contacts_store.get_active()
        self.theme = dict(THEMES.get(self.active_contact["relation"], THEMES["平辈"]))

        platform = self.config_data.get("platform", "微信")
        if platform not in PLATFORMS:
            platform = "微信"
        self.platform_var = tk.StringVar(value=platform)
        self.reply_vars: list[tk.StringVar] = []
        self.style_label_vars: list[tk.StringVar] = []
        self.selected = tk.IntVar(value=0)
        self.relation_var = tk.StringVar(value=self.active_contact["relation"])
        self.contact_var = tk.StringVar(value=contacts_store.display_name(self.active_contact))
        self.status_var = tk.StringVar(
            value="准备就绪 · Ctrl+V 可粘贴截图 · 识别后自动生成"
        )
        self.auto_reply_var = tk.BooleanVar(
            value=bool(self.config_data.get("qq_auto_reply", False))
        )
        self._generating = False
        self._gen_token = 0
        self._reply_cards: list[ctk.CTkFrame] = []
        self._busy_dots = 0
        self._anim_job: str | None = None
        self._theme_widgets: list = []
        self._auto_job: str | None = None
        self._auto_last_text = ""
        self._auto_pending_text = ""
        self._auto_busy = False
        self._auto_send_once = False  # one-shot send without toggling checkbox
        self._auto_fail_streak = 0

        self.configure(fg_color=self.theme["bg"])
        self._build_ui()
        self._bind_paste_shortcuts()
        self._reload_contact_menu()
        self._apply_theme(animate=False)
        self._refresh_style_labels()
        self._refresh_platform_ui()
        self._refresh_im_status()
        self._pulse_status()
        if self.auto_reply_var.get() and self.platform_var.get() == "QQ":
            self._schedule_auto_reply()

    def _card(self, parent, **kwargs) -> ctk.CTkFrame:
        opts = {
            "fg_color": self.theme["card"],
            "corner_radius": 16,
            "border_width": 1,
            "border_color": self.theme["line"],
        }
        opts.update(kwargs)
        frame = ctk.CTkFrame(parent, **opts)
        self._theme_widgets.append(("card", frame))
        return frame

    def _build_ui(self) -> None:
        # Fixed header
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=18, pady=(14, 0))
        self.title_label = ctk.CTkLabel(
            self.header,
            text="聊天回复助手",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=22, weight="bold"),
            text_color=self.theme["ink"],
        )
        self.title_label.pack(anchor="w")
        self.status_label = ctk.CTkLabel(
            self.header,
            textvariable=self.status_var,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=self.theme["muted"],
        )
        self.status_label.pack(anchor="w", pady=(2, 0))
        self._theme_widgets.append(("ink_label", self.title_label))
        self._theme_widgets.append(("muted_label", self.status_label))

        # Contact + relation bar
        top = self._card(self)
        top.pack(fill="x", padx=18, pady=(12, 0))
        top_inner = ctk.CTkFrame(top, fg_color="transparent")
        top_inner.pack(fill="x", padx=12, pady=12)

        plat_row = ctk.CTkFrame(top_inner, fg_color="transparent")
        plat_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            plat_row,
            text="回复平台",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=self.theme["ink"],
        ).pack(side="left")
        self._theme_widgets.append(("ink_label", plat_row.winfo_children()[0]))
        self.platform_seg = ctk.CTkSegmentedButton(
            plat_row,
            values=list(PLATFORMS),
            variable=self.platform_var,
            command=self._on_platform_change,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            selected_color=self.theme["accent"],
            selected_hover_color=self.theme["accent_hover"],
            unselected_color=self.theme["soft"],
            unselected_hover_color=self.theme["chip"],
            text_color=self.theme["ink"],
        )
        self.platform_seg.pack(side="left", padx=(10, 12))
        self._theme_widgets.append(("seg", self.platform_seg))
        self.auto_reply_chk = ctk.CTkCheckBox(
            plat_row,
            text="QQ自动回复",
            variable=self.auto_reply_var,
            command=self._on_auto_reply_toggle,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=self.theme["ink"],
            fg_color=self.theme["accent"],
            hover_color=self.theme["accent_hover"],
        )
        self.auto_reply_chk.pack(side="left")
        self._theme_widgets.append(("ink_label", self.auto_reply_chk))

        row1 = ctk.CTkFrame(top_inner, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(
            row1,
            text="聊天对象",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=self.theme["ink"],
        ).pack(side="left")
        self._theme_widgets.append(("ink_label", row1.winfo_children()[0]))

        self.contact_menu = ctk.CTkOptionMenu(
            row1,
            variable=self.contact_var,
            values=["默认平辈（平辈）"],
            command=self._on_contact_menu,
            width=220,
            height=32,
            fg_color=self.theme["soft"],
            button_color=self.theme["accent"],
            button_hover_color=self.theme["accent_hover"],
            text_color=self.theme["ink"],
            dropdown_fg_color="#FFFFFF",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        )
        self.contact_menu.pack(side="left", padx=(10, 8))
        self._theme_widgets.append(("soft_menu", self.contact_menu))

        ctk.CTkButton(
            row1,
            text="新建",
            width=64,
            height=32,
            command=self._new_contact,
            fg_color=self.theme["accent"],
            hover_color=self.theme["accent_hover"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(side="left", padx=(0, 6))
        self._theme_widgets.append(("accent_btn", row1.winfo_children()[-1]))

        ctk.CTkButton(
            row1,
            text="编辑",
            width=64,
            height=32,
            command=self._edit_contact,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=self.theme["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            row1,
            text="删除",
            width=64,
            height=32,
            command=self._delete_contact,
            fg_color="#F1F5F9",
            hover_color="#FEE2E2",
            text_color="#9A3412",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(side="left")

        row2 = ctk.CTkFrame(top_inner, fg_color="transparent")
        row2.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(
            row2,
            text="身份底色",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=self.theme["ink"],
        ).pack(side="left")
        self._theme_widgets.append(("ink_label", row2.winfo_children()[0]))
        self.relation_seg = ctk.CTkSegmentedButton(
            row2,
            values=list(RELATION_OPTIONS),
            variable=self.relation_var,
            command=self._on_relation_change,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            selected_color=self.theme["accent"],
            selected_hover_color=self.theme["accent_hover"],
            unselected_color=self.theme["soft"],
            unselected_hover_color=self.theme["chip"],
            text_color=self.theme["ink"],
        )
        self.relation_seg.pack(side="right")
        self._theme_widgets.append(("seg", self.relation_seg))

        self.style_hint = ctk.CTkLabel(
            top_inner,
            text="",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=self.theme["muted"],
        )
        self.style_hint.pack(fill="x", pady=(10, 0))
        self._theme_widgets.append(("muted_label", self.style_hint))

        # Scrollable middle
        self.scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
        )
        self.scroll.pack(fill="both", expand=True, padx=18, pady=(12, 0))

        msg_card = self._card(self.scroll)
        msg_card.pack(fill="x")
        msg_inner = ctk.CTkFrame(msg_card, fg_color="transparent")
        msg_inner.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(
            msg_inner,
            text="对话上下文（自动区分 对方 / 自己）",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=self.theme["ink"],
        ).pack(anchor="w")
        self._theme_widgets.append(("ink_label", msg_inner.winfo_children()[0]))
        self.incoming = ctk.CTkTextbox(
            msg_inner,
            height=130,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=14),
            fg_color="#F8FAFC",
            text_color=self.theme["ink"],
            border_width=1,
            border_color=self.theme["line"],
            corner_radius=12,
            wrap="word",
        )
        self.incoming.pack(fill="x", pady=(8, 10))
        self._theme_widgets.append(("textbox", self.incoming))

        btn_row = ctk.CTkFrame(msg_inner, fg_color="transparent")
        btn_row.pack(fill="x")
        self.btn_snip = ctk.CTkButton(
            btn_row,
            text="截图识别",
            command=self.snip_recognize,
            width=110,
            height=36,
            corner_radius=10,
            fg_color=self.theme["accent"],
            hover_color=self.theme["accent_hover"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        )
        self.btn_snip.pack(side="left", padx=(0, 8))
        self._theme_widgets.append(("accent_btn", self.btn_snip))

        self.btn_wx = ctk.CTkButton(
            btn_row,
            text="读取微信",
            command=self.load_from_im,
            width=100,
            height=36,
            corner_radius=10,
            fg_color="#334155",
            hover_color="#1E293B",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        )
        self.btn_wx.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="重新生成",
            command=lambda: self.on_generate(manual=True),
            width=88,
            height=36,
            corner_radius=10,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=self.theme["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="对调身份",
            command=self.flip_roles,
            width=88,
            height=36,
            corner_radius=10,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=self.theme["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        ).pack(side="left")

        self._more_btn = ctk.CTkButton(
            btn_row,
            text="更多 ▾",
            width=84,
            height=36,
            corner_radius=10,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=self.theme["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            command=self._open_more_menu,
        )
        self._more_btn.pack(side="right")

        ctk.CTkLabel(
            self.scroll,
            text="候选回复 · 点选后可改字，识别后自动生成",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=self.theme["muted"],
        ).pack(anchor="w", pady=(14, 6))
        self._theme_widgets.append(("muted_label", self.scroll.winfo_children()[-1]))

        self.replies_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.replies_frame.pack(fill="x")
        self._copy_icon = copy_icon(18)
        self._rebuild_reply_cards()

        # Fixed footer always visible
        self.footer = ctk.CTkFrame(self, fg_color=self.theme["bg"], corner_radius=0)
        self.footer.pack(fill="x", side="bottom", padx=18, pady=14)
        self._theme_widgets.append(("bg_frame", self.footer))

        self.btn_fill = ctk.CTkButton(
            self.footer,
            text="仅填入微信",
            command=lambda: self.send(False),
            width=120,
            height=42,
            corner_radius=12,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=self.theme["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        )
        self.btn_fill.pack(side="left", padx=(0, 8))

        self.btn_send = ctk.CTkButton(
            self.footer,
            text="发送到微信",
            command=lambda: self.send(True),
            width=150,
            height=42,
            corner_radius=12,
            fg_color=self.theme["accent"],
            hover_color=self.theme["accent_hover"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=14, weight="bold"),
        )
        self.btn_send.pack(side="left")
        self._theme_widgets.append(("accent_btn", self.btn_send))

        self.auto_send_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_send", False))
        )
        self.auto_send_chk = ctk.CTkCheckBox(
            self.footer,
            text="生成后自动发送",
            variable=self.auto_send_var,
            command=self._persist_auto_send,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=self.theme["ink"],
            fg_color=self.theme["accent"],
            hover_color=self.theme["accent_hover"],
        )
        self.auto_send_chk.pack(side="right")
        self._theme_widgets.append(("ink_label", self.auto_send_chk))

        self._highlight_selected()
        self._update_style_hint()

    # ----- theme / animation -----
    def _apply_theme(self, *, animate: bool = True) -> None:
        target = THEMES.get(self.relation_var.get(), THEMES["平辈"])
        if not animate:
            self.theme = dict(target)
            self._paint_theme(self.theme)
            return
        if self._anim_job:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
        start = dict(self.theme)
        steps = 10

        def step(i: int = 0) -> None:
            t = i / steps
            cur = {k: _lerp_hex(start[k], target[k], t) for k in target}
            self.theme = cur
            self._paint_theme(cur)
            if i < steps:
                self._anim_job = self.after(22, lambda: step(i + 1))
            else:
                self.theme = dict(target)
                self._paint_theme(self.theme)
                self._anim_job = None

        step(0)

    def _paint_theme(self, theme: dict) -> None:
        self.configure(fg_color=theme["bg"])
        for kind, widget in self._theme_widgets:
            try:
                if kind == "bg_frame":
                    widget.configure(fg_color=theme["bg"])
                elif kind == "card":
                    widget.configure(fg_color=theme["card"], border_color=theme["line"])
                elif kind == "ink_label":
                    widget.configure(text_color=theme["ink"])
                elif kind == "muted_label":
                    widget.configure(text_color=theme["muted"])
                elif kind == "accent_btn":
                    widget.configure(
                        fg_color=theme["accent"], hover_color=theme["accent_hover"]
                    )
                elif kind == "soft_menu":
                    widget.configure(
                        fg_color=theme["soft"],
                        button_color=theme["accent"],
                        button_hover_color=theme["accent_hover"],
                        text_color=theme["ink"],
                    )
                elif kind == "seg":
                    widget.configure(
                        selected_color=theme["accent"],
                        selected_hover_color=theme["accent_hover"],
                        unselected_color=theme["soft"],
                        unselected_hover_color=theme["chip"],
                        text_color=theme["ink"],
                    )
                elif kind == "radio":
                    widget.configure(
                        fg_color=theme["accent"], hover_color=theme["accent_hover"]
                    )
                elif kind == "entry":
                    widget.configure(
                        border_color=theme["line"], text_color=theme["ink"]
                    )
                elif kind == "textbox":
                    widget.configure(
                        border_color=theme["line"], text_color=theme["ink"]
                    )
            except Exception:
                pass
        self._highlight_selected()

    # ----- contacts -----
    def _reload_contact_menu(self) -> None:
        items = contacts_store.list_contacts()
        labels = [contacts_store.display_name(c) for c in items]
        self._contact_label_to_id = {
            contacts_store.display_name(c): c["id"] for c in items
        }
        self.contact_menu.configure(values=labels or ["（无）"])
        active = contacts_store.get_active()
        self.active_contact = active
        self.contact_var.set(contacts_store.display_name(active))
        self.relation_var.set(active["relation"])
        self._update_style_hint()

    def _update_style_hint(self) -> None:
        c = self.active_contact
        style = (c.get("style") or "").strip()
        prefix = c.get("reply_prefix", "自动：")
        modes = contacts_store.effective_modes(c)
        mode_names = " / ".join(m["name"] for m in modes)
        bits = [f"前缀「{prefix or '（无）'}」", f"方式：{mode_names}"]
        if style:
            bits.insert(0, f"人设：{style}")
        self.style_hint.configure(text=" · ".join(bits))

    def _on_contact_menu(self, label: str) -> None:
        cid = self._contact_label_to_id.get(label)
        if not cid:
            return
        self.active_contact = contacts_store.set_active(cid)
        self.relation_var.set(self.active_contact["relation"])
        self._update_style_hint()
        self._refresh_style_labels()
        self._apply_theme(animate=True)
        self.status_var.set(f"已切换到「{self.active_contact['name']}」")
        incoming = self.incoming.get("0.0", "end").strip()
        if incoming:
            self.on_generate(manual=False)

    def _new_contact(self) -> None:
        dlg = ContactDialog(self, title="新建聊天对象")
        self.wait_window(dlg)
        if not dlg.result:
            return
        item = contacts_store.upsert_contact(**dlg.result)
        self.active_contact = item
        self._reload_contact_menu()
        self._refresh_style_labels()
        self._apply_theme(animate=True)
        self.status_var.set(f"已新建「{item['name']}」")

    def _edit_contact(self) -> None:
        dlg = ContactDialog(self, title="编辑聊天对象", initial=self.active_contact)
        self.wait_window(dlg)
        if not dlg.result:
            return
        item = contacts_store.upsert_contact(
            contact_id=self.active_contact["id"], **dlg.result
        )
        self.active_contact = item
        contacts_store.set_active(item["id"])
        self._reload_contact_menu()
        self._refresh_style_labels()
        self._apply_theme(animate=True)
        self.status_var.set(f"已更新「{item['name']}」")
        if self.incoming.get("0.0", "end").strip():
            self.on_generate(manual=False)

    def _delete_contact(self) -> None:
        name = self.active_contact["name"]
        if not messagebox.askyesno("确认删除", f"删除聊天对象「{name}」？"):
            return
        try:
            contacts_store.delete_contact(self.active_contact["id"])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("无法删除", str(e))
            return
        self.active_contact = contacts_store.get_active()
        self._reload_contact_menu()
        self._refresh_style_labels()
        self._apply_theme(animate=True)
        self.status_var.set(f"已删除「{name}」")

    # ----- interaction helpers -----
    def _open_more_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="导入截图…", command=self.import_screenshot)
        menu.add_command(label="剪贴板图片", command=self.load_clipboard_image)
        menu.add_command(label="剪贴板文字", command=self.load_clipboard)
        menu.add_separator()
        menu.add_command(label="检测聊天窗口", command=self._refresh_im_status)
        try:
            x = self._more_btn.winfo_rootx()
            y = self._more_btn.winfo_rooty() + self._more_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _select_card(self, idx: int) -> None:
        self.selected.set(idx)
        self._highlight_selected()

    def _highlight_selected(self) -> None:
        idx = self.selected.get()
        for i, card in enumerate(self._reply_cards):
            if i == idx:
                card.configure(border_color=self.theme["accent"], border_width=2)
            else:
                card.configure(border_color=self.theme["line"], border_width=1)

    def _pulse_status(self) -> None:
        if self._generating:
            self._busy_dots = (self._busy_dots + 1) % 4
            dots = "." * self._busy_dots
            base = getattr(self, "_busy_base", "正在生成回复")
            self.status_var.set(f"{base}{dots}")
        self.after(400, self._pulse_status)

    def _on_relation_change(self, _value: str | None = None) -> None:
        # Sync relation into active contact; keep custom modes/prefix/style
        rel = self.relation_var.get()
        try:
            updated = contacts_store.upsert_contact(
                contact_id=self.active_contact["id"],
                name=self.active_contact["name"],
                relation=rel,
                style=self.active_contact.get("style", ""),
                reply_prefix=self.active_contact.get("reply_prefix", "自动："),
                modes=self.active_contact.get("modes") or [],
            )
            self.active_contact = updated
            contacts_store.set_active(updated["id"])
            self._reload_contact_menu()
        except Exception:
            pass
        self._refresh_style_labels()
        self._apply_theme(animate=True)
        incoming = self.incoming.get("0.0", "end").strip()
        if incoming:
            self.status_var.set(f"已切换「{rel}」主题，正在重生成…")
            self.on_generate(manual=False)
        else:
            self.status_var.set(f"「{self.active_contact['name']}」· {rel}主题")

    def _rebuild_reply_cards(self) -> None:
        """Build 3–4 candidate rows with copy icon on the right."""
        modes = contacts_store.effective_modes(self.active_contact)
        n = max(3, min(MAX_MODES, len(modes)))
        # clear old
        for w in self.replies_frame.winfo_children():
            w.destroy()
        self.reply_vars.clear()
        self.style_label_vars.clear()
        self._reply_cards.clear()

        for i in range(n):
            var = tk.StringVar()
            label_var = tk.StringVar(value=f"候选 {i + 1}")
            self.reply_vars.append(var)
            self.style_label_vars.append(label_var)

            card = self._card(self.replies_frame)
            card.pack(fill="x", pady=4)
            self._reply_cards.append(card)

            top_r = ctk.CTkFrame(card, fg_color="transparent")
            top_r.pack(fill="x", padx=12, pady=(10, 0))
            ctk.CTkRadioButton(
                top_r,
                text="",
                variable=self.selected,
                value=i,
                width=22,
                fg_color=self.theme["accent"],
                hover_color=self.theme["accent_hover"],
                command=self._highlight_selected,
            ).pack(side="left")
            self._theme_widgets.append(("radio", top_r.winfo_children()[0]))
            title = ctk.CTkLabel(
                top_r,
                textvariable=label_var,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
                text_color=self.theme["ink"],
            )
            title.pack(side="left", padx=(6, 0))
            self._theme_widgets.append(("ink_label", title))
            title.bind("<Button-1>", lambda _e, idx=i: self._select_card(idx))
            card.bind("<Button-1>", lambda _e, idx=i: self._select_card(idx))

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(6, 12))
            entry = ctk.CTkEntry(
                row,
                textvariable=var,
                height=36,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=14),
                fg_color="#F8FAFC",
                border_color=self.theme["line"],
                text_color=self.theme["ink"],
                corner_radius=10,
            )
            entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
            self._theme_widgets.append(("entry", entry))
            copy_btn = ctk.CTkButton(
                row,
                text="",
                image=self._copy_icon,
                width=36,
                height=36,
                corner_radius=10,
                fg_color="#F1F5F9",
                hover_color="#E2E8F0",
                command=lambda idx=i: self._copy_reply(idx),
            )
            copy_btn.pack(side="right")

        self._refresh_style_labels()
        self._highlight_selected()

    def _copy_reply(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.reply_vars):
            return
        text = self.reply_vars[idx].get().strip()
        if not text:
            self.status_var.set("这条还是空的，先生成或填写")
            return
        try:
            set_text(text)
            self.selected.set(idx)
            self._highlight_selected()
            self.status_var.set("已复制到剪贴板")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("复制失败", str(e))

    def _refresh_style_labels(self) -> None:
        modes = contacts_store.effective_modes(self.active_contact)
        # slot count may change with relation / contact
        if len(self.reply_vars) != max(3, min(MAX_MODES, len(modes))):
            self._rebuild_reply_cards()
            return
        labels = style_labels_for(self.relation_var.get(), modes)
        for i, label_var in enumerate(self.style_label_vars):
            label_var.set(labels[i] if i < len(labels) else f"候选 {i + 1}")

    def flip_roles(self) -> None:
        text = self.incoming.get("0.0", "end").strip()
        if not text:
            messagebox.showinfo("提示", "没有可对调的对话")
            return
        flipped = flip_dialogue_roles(text)
        self.incoming.delete("0.0", "end")
        self.incoming.insert("0.0", flipped)
        self.status_var.set("已对调对方/自己，正在重生成…")
        self.on_generate(manual=False)

    def _platform_name(self) -> str:
        return self.platform_var.get() if self.platform_var.get() in PLATFORMS else "微信"

    def _persist_config_patch(self, **kwargs) -> None:
        try:
            cfg = load_config()
            cfg.update(kwargs)
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.config_data = cfg
        except Exception:
            pass

    def _on_platform_change(self, _value: str | None = None) -> None:
        self._persist_config_patch(platform=self._platform_name())
        self._refresh_platform_ui()
        self._refresh_im_status()
        if self._platform_name() != "QQ":
            self._stop_auto_reply()
        elif self.auto_reply_var.get():
            self._schedule_auto_reply()

    def _refresh_platform_ui(self) -> None:
        name = self._platform_name()
        self.btn_wx.configure(text=f"读取{name}")
        self.btn_fill.configure(text=f"仅填入{name}")
        self.btn_send.configure(text=f"发送到{name}")
        # QQ auto checkbox only meaningful on QQ
        try:
            if name == "QQ":
                self.auto_reply_chk.configure(state="normal")
            else:
                self.auto_reply_chk.configure(state="disabled")
        except Exception:
            pass

    def _persist_auto_send(self) -> None:
        self._persist_config_patch(auto_send=bool(self.auto_send_var.get()))

    def _on_auto_reply_toggle(self) -> None:
        on = bool(self.auto_reply_var.get())
        self._persist_config_patch(qq_auto_reply=on)
        if on and self._platform_name() == "QQ":
            self.status_var.set("QQ自动回复已开启 · 发现对方新消息会生成并发送")
            self._schedule_auto_reply()
        else:
            self._stop_auto_reply()
            self.status_var.set("QQ自动回复已关闭")

    def _stop_auto_reply(self) -> None:
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None
        self._auto_busy = False

    def _schedule_auto_reply(self) -> None:
        self._stop_auto_reply()
        if not self.auto_reply_var.get() or self._platform_name() != "QQ":
            return
        interval = int(self.config_data.get("qq_auto_interval_sec", 12) or 12)
        interval = max(6, min(60, interval))
        self._auto_job = self.after(interval * 1000, self._auto_reply_tick)

    def _auto_reply_tick(self) -> None:
        self._auto_job = None
        if not self.auto_reply_var.get() or self._platform_name() != "QQ":
            return
        if self._auto_busy or self._generating:
            self._schedule_auto_reply()
            return

        def work() -> None:
            try:
                title = load_config().get("qq_window_title", "QQ")
                text = capture_qq_dialogue(title)
                self.after(0, lambda: self._auto_reply_got(text))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._auto_reply_err(str(e)))

        self._auto_busy = True
        self.status_var.set("QQ自动巡检中…")
        threading.Thread(target=work, daemon=True).start()

    def _auto_reply_err(self, msg: str) -> None:
        self._auto_busy = False
        self.status_var.set(f"QQ自动巡检失败：{msg[:60]}")
        self._schedule_auto_reply()

    def _auto_reply_got(self, text: str) -> None:
        self._auto_busy = False
        text = (text or "").strip()
        if not text:
            self._schedule_auto_reply()
            return
        if text == self._auto_last_text:
            self.status_var.set("QQ自动 · 无新消息")
            self._schedule_auto_reply()
            return
        # Only reply when the latest turn looks like 对方
        last_role = ""
        for line in text.splitlines():
            if line.startswith("对方：") or line.startswith("对方:"):
                last_role = "对方"
            elif line.startswith("自己：") or line.startswith("自己:"):
                last_role = "自己"
        if last_role == "自己":
            self._auto_last_text = text
            self.status_var.set("QQ自动 · 最新是自己发的，跳过")
            self._schedule_auto_reply()
            return
        if not last_role:
            # 无对方/自己标记时容易误触，跳过并记为已见
            self._auto_last_text = text
            self.status_var.set("QQ自动 · 对话未区分身份，跳过")
            self._schedule_auto_reply()
            return

        self._auto_pending_text = text
        self.incoming.delete("0.0", "end")
        self.incoming.insert("0.0", text)
        self.status_var.set("QQ自动 · 发现对方消息，正在生成并发送…")
        # one-shot send; do NOT flip the user's「生成后自动发送」checkbox
        self._auto_send_once = True
        self.on_generate(manual=False)
        # reschedule after generate finishes (see _apply_replies / _fail)

    def _refresh_im_status(self) -> None:
        if self._generating:
            return
        who = self.active_contact["name"]
        rel = self.relation_var.get()
        name = self._platform_name()
        if name == "QQ":
            title = self.config_data.get("qq_window_title", "QQ")
            hwnd = find_qq_window(title)
        else:
            title = self.config_data.get("window_title", "微信")
            hwnd = find_wechat_window(title)
        auto = " · 自动回复开" if (name == "QQ" and self.auto_reply_var.get()) else ""
        if hwnd:
            self.status_var.set(f"{name}已连接 · {who}（{rel}）{auto}")
        else:
            self.status_var.set(f"未找到{name} · 当前 {who}（{rel}）")

    def load_clipboard(self) -> None:
        text = get_text().strip()
        if not text:
            messagebox.showinfo("提示", "剪贴板没有文字。若刚截了图，请用「剪贴板图片」")
            return
        self._apply_incoming(text, "已读取剪贴板文字，正在生成…", auto_generate=True)

    def snip_recognize(self) -> None:
        self.status_var.set("请框选聊天记录…截完后自动识别并生成")
        self.update_idletasks()
        try:
            self.iconify()
        except Exception:
            pass

        def work() -> None:
            try:
                text = snip_and_recognize(timeout_sec=90.0)
                self.after(0, lambda: self._after_snip_ok(text))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._after_snip_fail(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _after_snip_ok(self, text: str) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._apply_incoming(text, "截图识别完成，正在生成…", auto_generate=True)

    def _after_snip_fail(self, msg: str) -> None:
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass
        self._capture_fail(msg)

    def import_screenshot(self) -> None:
        path = filedialog.askopenfilename(
            title="选择聊天截图",
            filetypes=[
                ("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.status_var.set("正在识别导入的截图…")

        def work() -> None:
            try:
                text = recognize_from_path(path)
                self.after(
                    0,
                    lambda: self._apply_incoming(
                        text, "导入识别完成，正在生成…", auto_generate=True
                    ),
                )
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._capture_fail(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _bind_paste_shortcuts(self) -> None:
        # Ctrl+V / Shift+Insert → prefer clipboard image, else ignore (let textboxes paste text)
        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            self.bind_all(seq, self._on_global_paste)

    def _on_global_paste(self, event=None):
        # If focus is in a CTk text/entry and clipboard is text-only, allow default paste
        try:
            from PIL import ImageGrab

            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None
        has_image = isinstance(clip, Image.Image) or (
            isinstance(clip, list) and bool(clip)
        )
        if not has_image:
            return None  # let widget handle text paste
        self.load_clipboard_image()
        return "break"

    def load_clipboard_image(self) -> None:
        self.status_var.set("正在识别粘贴的图片…")

        def work() -> None:
            try:
                text = recognize_from_clipboard_image()
                self.after(
                    0,
                    lambda: self._apply_incoming(
                        text, "粘贴图片识别完成，正在生成…", auto_generate=True
                    ),
                )
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._capture_fail(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def load_from_im(self) -> None:
        name = self._platform_name()
        self.status_var.set(f"正在读取{name}…")
        try:
            self.iconify()
        except Exception:
            pass
        self.update_idletasks()

        def work() -> None:
            try:
                import time

                time.sleep(0.55)
                cfg = load_config()
                if name == "QQ":
                    text = capture_qq_dialogue(cfg.get("qq_window_title", "QQ"))
                else:
                    text = capture_latest_incoming(cfg.get("window_title", "微信"))
                self.after(0, lambda: self._after_im_ok(text, name))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._after_im_fail(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _after_im_ok(self, text: str, name: str) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._auto_last_text = (text or "").strip()
        self._apply_incoming(text, f"{name}读取完成，正在生成…", auto_generate=True)

    def _after_im_fail(self, msg: str) -> None:
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass
        self._capture_fail(msg)

    def _apply_incoming(
        self,
        text: str,
        status: str = "已识别",
        *,
        auto_generate: bool = False,
    ) -> None:
        self.incoming.delete("0.0", "end")
        self.incoming.insert("0.0", text)
        self.status_var.set(status)
        if auto_generate and text.strip():
            self.on_generate(manual=False)

    def _capture_fail(self, msg: str) -> None:
        self._generating = False
        self.status_var.set("读取失败")
        messagebox.showerror("读取失败", msg)

    def on_generate(self, manual: bool = True) -> None:
        incoming = self.incoming.get("0.0", "end").strip()
        if not incoming:
            if manual:
                messagebox.showinfo("提示", "请先截图识别或填入对话")
            return

        relation = self.relation_var.get()
        contact = self.active_contact
        style = (contact.get("style") or "").strip() or load_config().get("style", "")
        modes = contacts_store.effective_modes(contact)
        reply_prefix = contact.get("reply_prefix", "自动：")
        if reply_prefix is None:
            reply_prefix = "自动："
        self._gen_token += 1
        token = self._gen_token
        self._generating = True
        self._busy_base = f"正在为「{contact['name']}」生成回复"
        self.status_var.set(self._busy_base + "…")

        def work() -> None:
            try:
                cfg = load_config()
                self.config_data = cfg
                replies = generate_replies(
                    incoming,
                    api_base=cfg.get("api_base", "https://api.deepseek.com/v1"),
                    api_key=cfg.get("api_key", ""),
                    model=cfg.get("model", "deepseek-chat"),
                    style=style,
                    relation=relation,
                    modes=modes,
                    reply_prefix=reply_prefix,
                    count=len(modes),
                )
                self.after(0, lambda: self._apply_replies(replies, token))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._fail(str(e), token))

        threading.Thread(target=work, daemon=True).start()

    def _apply_replies(self, replies: list[str], token: int) -> None:
        if token != self._gen_token:
            return
        self._generating = False
        for i, var in enumerate(self.reply_vars):
            var.set(replies[i] if i < len(replies) else "")
        self.selected.set(0)
        self._highlight_selected()
        plat = self._platform_name()
        self.status_var.set(
            f"已生成 · {self.active_contact['name']}（{self.relation_var.get()}）· {plat}"
        )
        should_send = self.auto_send_var.get() or self._auto_send_once
        was_auto = self._auto_send_once
        self._auto_send_once = False
        if was_auto and self._auto_pending_text:
            self._auto_last_text = self._auto_pending_text
            self._auto_pending_text = ""
            self._auto_fail_streak = 0
        if should_send:
            self.after(350, lambda: self.send(True))
        if self.auto_reply_var.get() and self._platform_name() == "QQ":
            # wait longer after send so OCR sees our reply
            delay = 5000 if was_auto else 0
            if delay:
                self.after(delay, self._schedule_auto_reply)
            else:
                self._schedule_auto_reply()

    def _fail(self, msg: str, token: int) -> None:
        if token != self._gen_token:
            return
        self._generating = False
        self._auto_send_once = False
        auto_pending = bool(self._auto_pending_text)
        if auto_pending:
            self._auto_fail_streak += 1
            # keep pending unmarked so next tick can retry; after 3 fails, mark seen
            if self._auto_fail_streak >= 3:
                self._auto_last_text = self._auto_pending_text
                self._auto_pending_text = ""
                self._auto_fail_streak = 0
        self.status_var.set("生成失败")
        # don't modal-block during QQ auto patrol
        if auto_pending and self.auto_reply_var.get() and self._platform_name() == "QQ":
            self.status_var.set(f"QQ自动 · 生成失败：{msg[:40]}")
            self._schedule_auto_reply()
        else:
            messagebox.showerror("生成失败", msg)

    def send(self, do_send: bool) -> None:
        idx = self.selected.get()
        if idx < 0 or idx >= len(self.reply_vars):
            messagebox.showinfo("提示", "请先选择一条回复")
            return
        text = self.reply_vars[idx].get().strip()
        if not text:
            messagebox.showinfo("提示", "选中的回复是空的，请先截图识别")
            return
        name = self._platform_name()
        self.status_var.set(f"正在切换到{name}…")
        # Hide helper so focus/Ctrl+V don't land back on this window
        try:
            self._unbind_paste_shortcuts()
            self.iconify()
        except Exception:
            pass
        self.update_idletasks()
        self.after(280, lambda: self._do_send(text, do_send))

    def _unbind_paste_shortcuts(self) -> None:
        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            try:
                self.unbind_all(seq)
            except Exception:
                pass

    def _do_send(self, text: str, do_send: bool) -> None:
        name = self._platform_name()
        err: str | None = None
        try:
            import time

            time.sleep(0.2)
            cfg = load_config()
            self.config_data = cfg
            if name == "QQ":
                qq_paste_and_send(
                    text,
                    title_hint=cfg.get("qq_window_title", "QQ"),
                    send=do_send,
                    ctrl_enter=bool(cfg.get("qq_ctrl_enter", False)),
                )
            else:
                wx_paste_and_send(
                    text,
                    title_hint=cfg.get("window_title", "微信"),
                    send=do_send,
                )
        except Exception as e:  # noqa: BLE001
            err = str(e)
        finally:
            try:
                self.deiconify()
                self.lift()
                self._bind_paste_shortcuts()
            except Exception:
                pass

        if err:
            self.status_var.set("发送失败")
            messagebox.showerror("发送失败", err)
        else:
            self.status_var.set(
                f"已发送到{name}" if do_send else f"已填入{name}输入框"
            )


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
