"""Persist chat contacts with per-person reply settings."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from reply_ai import MAX_MODES, RELATION_OPTIONS, default_modes_for

ROOT = Path(__file__).resolve().parent
CONTACTS_PATH = ROOT / "contacts.json"

DEFAULT_CONTACTS = [
    {
        "id": "default-peer",
        "name": "默认平辈",
        "relation": "平辈",
        "style": "自然口语，像朋友聊天",
        "reply_prefix": "自动：",
        "modes": [],
    },
    {
        "id": "default-elder",
        "name": "默认长辈",
        "relation": "长辈",
        "style": "尊敬简短，少用网络梗",
        "reply_prefix": "自动：",
        "modes": [],
    },
    {
        "id": "default-junior",
        "name": "默认晚辈",
        "relation": "晚辈",
        "style": "亲切平等，可轻松一点",
        "reply_prefix": "自动：",
        "modes": [],
    },
]


def _normalize_modes(raw: Any, relation: str) -> list[dict[str, str]]:
    """Keep custom modes; empty list means use relation defaults at runtime."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:MAX_MODES]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        guide = str(item.get("guide") or "").strip()
        if not name and not guide:
            continue
        out.append({"name": name or f"方式{len(out)+1}", "guide": guide})
    # partial custom → pad from defaults (平辈默认 4 条含阴阳怪气)
    defaults = default_modes_for(relation)
    need = max(3, len(defaults))
    if out and len(out) < need:
        for d in defaults:
            if len(out) >= need:
                break
            if any(m["name"] == d["name"] for m in out):
                continue
            out.append(dict(d))
    return out[:MAX_MODES]


def _normalize_contact(c: dict) -> dict[str, Any] | None:
    if not isinstance(c, dict):
        return None
    name = str(c.get("name") or "").strip() or "未命名"
    relation = c.get("relation") if c.get("relation") in RELATION_OPTIONS else "平辈"
    prefix = c.get("reply_prefix")
    if prefix is None:
        prefix = "自动："
    else:
        prefix = str(prefix)
    return {
        "id": str(c.get("id") or uuid.uuid4()),
        "name": name,
        "relation": relation,
        "style": str(c.get("style") or "").strip(),
        "reply_prefix": prefix,
        "modes": _normalize_modes(c.get("modes"), relation),
    }


def _empty_store() -> dict[str, Any]:
    return {
        "active_id": DEFAULT_CONTACTS[0]["id"],
        "contacts": [dict(c) for c in DEFAULT_CONTACTS],
    }


def load_store() -> dict[str, Any]:
    if not CONTACTS_PATH.exists():
        store = _empty_store()
        save_store(store)
        return store
    try:
        data = json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        store = _empty_store()
        save_store(store)
        return store
    cleaned = []
    for c in data.get("contacts") or []:
        item = _normalize_contact(c)
        if item:
            cleaned.append(item)
    if not cleaned:
        return _empty_store()
    active = data.get("active_id")
    ids = {c["id"] for c in cleaned}
    if active not in ids:
        active = cleaned[0]["id"]
    return {"active_id": active, "contacts": cleaned}


def save_store(store: dict[str, Any]) -> None:
    CONTACTS_PATH.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_contacts() -> list[dict[str, Any]]:
    return load_store()["contacts"]


def get_active() -> dict[str, Any]:
    store = load_store()
    for c in store["contacts"]:
        if c["id"] == store["active_id"]:
            return c
    return store["contacts"][0]


def set_active(contact_id: str) -> dict[str, Any]:
    store = load_store()
    ids = {c["id"] for c in store["contacts"]}
    if contact_id not in ids:
        raise ValueError("找不到该聊天对象")
    store["active_id"] = contact_id
    save_store(store)
    return get_active()


def upsert_contact(
    *,
    name: str,
    relation: str,
    style: str = "",
    reply_prefix: str = "自动：",
    modes: list[dict[str, str]] | None = None,
    contact_id: str | None = None,
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("请填写对象名称")
    if relation not in RELATION_OPTIONS:
        relation = "平辈"
    store = load_store()
    payload = {
        "name": name,
        "relation": relation,
        "style": style.strip(),
        "reply_prefix": reply_prefix if reply_prefix is not None else "自动：",
        "modes": _normalize_modes(modes or [], relation),
    }
    if contact_id:
        for c in store["contacts"]:
            if c["id"] == contact_id:
                c.update(payload)
                save_store(store)
                return dict(c)
        raise ValueError("找不到该聊天对象")
    item = {"id": str(uuid.uuid4()), **payload}
    store["contacts"].append(item)
    store["active_id"] = item["id"]
    save_store(store)
    return item


def delete_contact(contact_id: str) -> None:
    store = load_store()
    if len(store["contacts"]) <= 1:
        raise ValueError("至少保留一个聊天对象")
    store["contacts"] = [c for c in store["contacts"] if c["id"] != contact_id]
    if store["active_id"] == contact_id:
        store["active_id"] = store["contacts"][0]["id"]
    save_store(store)


def display_name(contact: dict[str, Any]) -> str:
    return f"{contact['name']}（{contact['relation']}）"


def effective_modes(contact: dict[str, Any]) -> list[dict[str, str]]:
    """Resolve speaking modes for UI / generation (平辈默认含阴阳怪气共 4 条)."""
    relation = contact.get("relation") or "平辈"
    defaults = default_modes_for(relation)
    modes = contact.get("modes") or []
    if len(modes) < 3:
        return defaults
    out = [dict(m) for m in modes[:MAX_MODES]]
    # 旧联系人只有 3 条时，为平辈自动补上「阴阳怪气」
    if relation == "平辈" and len(out) == 3 and not any("阴阳" in m.get("name", "") for m in out):
        yin = next((d for d in defaults if "阴阳" in d["name"]), None)
        if yin:
            out.append(dict(yin))
    return out
