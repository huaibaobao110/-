"""Generate casual WeChat reply suggestions via OpenAI-compatible API."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any


RELATION_OPTIONS = ("长辈", "平辈", "晚辈")

RELATION_STYLES: dict[str, tuple[str, ...]] = {
    "长辈": ("稳妥得体", "关心体贴", "轻松自然"),
    "平辈": ("正常风格", "暧昧风格", "搞笑风格", "阴阳怪气"),
    "晚辈": ("随和自然", "耐心回应", "轻松幽默"),
}

RELATION_RULES: dict[str, str] = {
    "长辈": (
        "对方是长辈（父母、长亲、老师等）。\n"
        "尊敬稳重，可适度用「您」；少用网络梗与夸张表情；禁止暧昧、油腻奉承。"
    ),
    "平辈": (
        "对方是平辈（朋友、同学、同事、恋人等）。\n"
        "口语自然；多种说话方式要明显不同，含正常/暧昧/搞笑/阴阳怪气。"
    ),
    "晚辈": (
        "对方是晚辈（弟弟妹妹、学生、更年轻的人）。\n"
        "亲切平等；可耐心说明、可轻松玩笑；禁止端着训人；\n"
        "不要无脑喊加油，除非对方明确在求助/诉苦。"
    ),
}

STYLE_GUIDES: dict[str, dict[str, str]] = {
    "长辈": {
        "稳妥得体": "礼貌清楚、句子完整一点；少语气词；几乎不用表情。",
        "关心体贴": "先回应事情，再带一句关心（身体/吃饭/别太累）；温柔但不肉麻。",
        "轻松自然": "仍尊敬，但更口语、短一些；最多一个温和语气词，如「哈哈」「嗯嗯」。",
    },
    "平辈": {
        "正常风格": "像普通朋友随口回：接住话题、信息清楚；0-1个自然语气词；表情克制。",
        "暧昧风格": (
            "必须明显偏暧昧，不能写成普通朋友口吻。"
            "要有亲昵感、占有欲一点点、拉近距离："
            "可撒娇、吃醋式关心、暗示想念/想见、用「你啊」「笨蛋」「嗯？」收尾；"
            "多用省略号、轻声、反问；可1-2个软表情（😊🥰😳😉）；"
            "禁止露骨色情，但撩意要够强，读起来能听出在撩。"
        ),
        "搞笑风格": "接梗、反转、轻吐槽、夸张一点点；可玩对方刚发的表情包语义；"
        "可用1个逗趣表情（如😂/🤣/👀）；不要阴阳怪气，不要冒犯。",
        "阴阳怪气": (
            "必须极度阴阳怪气，语气要冲、要刺、假笑感拉满，一眼听出在阴阳。"
            "表面客套礼貌，实际冷嘲挖苦、刺痛对方："
            "句式多用「挺好的呀」「行吧您开心就好」「不回也正常呢」「原来在忙啊，懂了」"
            "「哦？这样啊」「真会说话」「辛苦你了哈」「那你继续呗」；"
            "可叠「哈」「呵」「哦？」「是吗」「呢」和省略号；可带假笑😊或👀；"
            "禁止脏话辱骂和违法内容；禁止写成普通吐槽/搞笑；阴阳力度必须够强。"
        ),
    },
    "晚辈": {
        "随和自然": "平等口语，像熟人聊天；简短直接。",
        "耐心回应": "把对方没说清的点讲明白/给个可行建议；语气温和，不说教。",
        "轻松幽默": "温和玩笑或俏皮接话；可轻微调侃但不贬低；表情最多1个。",
    },
}

MAX_MODES = 4


def default_modes_for(relation: str) -> list[dict[str, str]]:
    labels = RELATION_STYLES.get(relation, RELATION_STYLES["平辈"])
    guides = STYLE_GUIDES.get(relation, STYLE_GUIDES["平辈"])
    return [{"name": lab, "guide": guides.get(lab, "")} for lab in labels]


def style_labels_for(
    relation: str,
    modes: list[dict[str, str]] | None = None,
) -> tuple[str, ...]:
    if modes and len(modes) >= 3:
        return tuple(
            str(m.get("name") or f"方式{i+1}") for i, m in enumerate(modes[:MAX_MODES])
        )
    return RELATION_STYLES.get(relation, RELATION_STYLES["平辈"])


def resolve_modes(
    relation: str,
    modes: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    defaults = default_modes_for(relation)
    if modes and len(modes) >= 3:
        out = []
        for i, m in enumerate(modes[:MAX_MODES]):
            fallback = defaults[i] if i < len(defaults) else {"name": f"方式{i+1}", "guide": ""}
            name = str(m.get("name") or "").strip() or fallback["name"]
            guide = str(m.get("guide") or "").strip() or fallback["guide"]
            out.append({"name": name, "guide": guide})
        return out
    return defaults


def _system_prompt(relation: str, persona: str, modes: list[dict[str, str]]) -> str:
    rules = RELATION_RULES.get(relation, RELATION_RULES["平辈"])
    guide_lines = "\n".join(f"- {m['name']}：{m['guide']}" for m in modes)
    n = len(modes)
    persona_block = (
        f"用户为本对象设定的【说话方式/人设】（最高优先级，{n}条都必须遵守）：\n{persona}\n"
        if persona.strip()
        else "未指定专属人设时，按身份默认语气。\n"
    )
    return f"""你在代替「用户」写下一条微信回复。发出去的话 = 「自己」。

输入可能是单句，也可能是带角色的历史对话：
对方：……
自己：……
对方：[表情包] / 😂 / 嗯

角色：
- 「对方」= 聊天对象；「自己」= 用户已发送内容
- 写用户的下一条（接在最后一轮后），要吃透上下文
- 若角色可能标反了，以语义判断谁在问、谁在答，仍生成「自己」该说的话

对方身份：{relation}
{rules}

{persona_block}
共 {n} 条候选，必须按下面「说话方式」生成，顺序固定；
每种方式都要在【人设】约束下改写话术（同一人设、不同语气/角度），不能无视人设：
{guide_lines}

表情与符号：
- 对话里可能出现 emoji、[表情包:开心/宠溺/搞笑/委屈/冷淡/无语]、颜文字
- 看到 [表情包:…] 要按情绪标签接话，不要当没看见
- 不要刷屏堆表情

硬性要求：
- 错误：对方说「你要考公啊」→「加油你一定行」
- 正确：对方说「你要考公啊」→「嗯在看呢」/「还没定」
- 各条内容不能雷同（别只改两三个字）
- 不要重复自己刚说过的整句
- 不要书面语、客服腔；不要解释；不要加引号；不要写风格/角色标签
- 只输出 JSON 字符串数组，长度必须是 {n}
- 默认简体中文
"""


DEFAULT_FALLBACKS = [
    "glm-4-flash-250414",
    "glm-4.7-flash",
    "glm-4-flash",
]

STYLE_LABELS = RELATION_STYLES["平辈"]


def _extract_json_list(text: str) -> list[str] | None:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            return None
    return None


def _parse_lines(text: str, count: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[\d]+[\.\)、]\s*", "", line)
        line = re.sub(r"^[^:：]{2,8}[:：]\s*", "", line)
        line = line.strip("「」\"'“”")
        if line:
            lines.append(line)
        if len(lines) >= count:
            break
    return lines


def _with_prefix(text: str, prefix: str) -> str:
    text = text.strip()
    # Only strip a real prefix like「自动：」, never bare「自动回复…」
    text = re.sub(r"^自动[：:]\s*", "", text).strip()
    if not prefix:
        return text
    core = prefix.rstrip("：: ").strip()
    if core:
        # require colon when core is short Chinese like「自动」, else allow optional
        if core == "自动":
            text = re.sub(r"^自动[：:]\s*", "", text).strip()
        else:
            text = re.sub(
                rf"^{re.escape(prefix)}\s*",
                "",
                text,
            ).strip()
            text = re.sub(rf"^{re.escape(core)}[：:]\s*", "", text).strip()
    return f"{prefix}{text}"


class ContentFilterError(RuntimeError):
    """Zhipu / model safety filter blocked the request."""


class TransientAPIError(RuntimeError):
    """Temporary network / server error; safe to retry."""


def _is_content_filter(detail: str) -> bool:
    return (
        "1301" in detail
        or "contentFilter" in detail
        or "不安全" in detail
        or "敏感内容" in detail
    )


def _is_transient(code: int, detail: str) -> bool:
    if code in {408, 425, 500, 502, 503, 504}:
        return True
    return (
        "1234" in detail
        or "网络错误" in detail
        or "请稍后重试" in detail
        or "timeout" in detail.lower()
        or "temporarily" in detail.lower()
    )


def _friendly_http_error(code: int, detail: str) -> str:
    if _is_content_filter(detail):
        return (
            "内容安全拦截：人设/对话里的措辞太冲。"
            "已尽量自动改成「损友吐槽」表述；若仍失败，请把专属话术改温和一点再生成。"
        )
    if code == 429 or "1305" in detail or "访问量过大" in detail or "速率" in detail:
        return "模型正忙/限流，请稍后再点「生成回复」（已自动尝试备用模型）"
    if _is_transient(code, detail):
        return "智谱服务暂时网络异常，请稍后再点「重新生成」（已自动重试过）"
    if code in {401, 403}:
        return "API Key 无效或无权限，请检查 config.json"
    return f"API 请求失败 HTTP {code}: {detail[:300]}"


def _soften_text(text: str) -> str:
    """Rewrite harsh persona wording so Zhipu safety filter is less likely to trip."""
    if not text:
        return text
    t = text
    reps = [
        (r"傻逼|傻叉|傻B|傻b|\bsb\b|\bSB\b|\bSb\b", "爱抬杠的损友"),
        (r"纯骂他就行了|纯骂他|使劲骂|骂死他|骂回去|骂他", "用损友互怼口吻吐槽他、别惯着"),
        (r"骂人|开骂|爆粗", "损友式吐槽"),
        (r"骂", "怼/吐槽"),
        (r"去死|滚吧|滚啊", "一边去"),
        (r"操你|肏|屌丝|杂种|贱人|畜生", "这家伙"),
        (r"露骨色情", "过分亲密"),
        (r"撩意要够强", "暧昧感要明显"),
    ]
    for pat, rep in reps:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    return t


def _soften_modes(modes: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for m in modes:
        name = _soften_text(str(m.get("name") or ""))
        guide = _soften_text(str(m.get("guide") or ""))
        # roast persona + 暧昧 often conflicts / filters; remap to 阴阳损
        if "暧昧" in name and any(k in guide for k in ("撩", "想见", "占有")):
            # keep label but tone down explicit flirt instructions slightly
            guide = (
                "语气更近、带点调侃拉扯；可轻声反问、省略号；"
                "像损友里夹一点暧昧试探，点到为止，不要露骨。"
            )
        out.append({"name": name or m.get("name", ""), "guide": guide})
    return out


def _chat_once(
    *,
    incoming: str,
    api_base: str,
    api_key: str,
    model: str,
    relation: str,
    style: str,
    modes: list[dict[str, str]],
    reply_prefix: str,
    timeout: float,
    soft: bool = False,
) -> list[str]:
    # Always soften persona wording; soft=True also softens dialogue + modes harder
    style_use = _soften_text(style)
    if soft:
        modes_use = _soften_modes(
            [{**m, "guide": _soften_text(m.get("guide", ""))} for m in modes]
        )
        incoming_use = _soften_text(incoming)
    else:
        modes_use = [{**m, "guide": _soften_text(m.get("guide", ""))} for m in modes]
        incoming_use = incoming

    labels = tuple(m["name"] for m in modes_use)
    n = len(modes_use)
    label_join = " / ".join(labels)
    has_roles = (
        ("对方：" in incoming_use)
        or ("自己：" in incoming_use)
        or ("对方:" in incoming_use)
        or ("自己:" in incoming_use)
    )
    context_hint = (
        "下面是历史对话（已区分对方/自己，可能含表情/表情包标记）。请生成用户（自己）的下一条回复。"
        if has_roles
        else "下面是对方消息（可能含多段、emoji、[表情包]）。请生成用户的回复。"
    )
    style_lines = "；".join(f"{m['name']}={m['guide']}" for m in modes_use)
    roast = any(k in style_use for k in ("吐槽", "怼", "损友", "抬杠"))
    example = json.dumps([f"回复{i+1}" for i in range(n)], ensure_ascii=False)
    user_prompt = (
        f"【人设/说话总则】（必须遵守，{n}条都要体现）：{style_use or '无'}\n"
        f"对方身份：{relation}\n"
        f"{context_hint}\n"
        f"请严格按说话方式生成 {n} 条：{label_join}。\n"
        f"各方式要点：{style_lines}\n"
        "每条都要在人设约束下，按该方式改写话术；人设与默认礼貌冲突时，以人设为准。\n"
        "用口语短句；可以损、可以怼，但不要人身攻击脏话，不要违法违规内容。\n"
        f"返回 JSON 数组，如 {example}。{n}条要明显不一样。\n"
        "若对方发了表情包/emoji/[表情包:情绪]，请接住情绪再回。不要输出角色前缀。\n"
    )
    if roast:
        user_prompt += (
            "人设偏损友互怼：各条都要带点嘲讽/抬杠，但用玩笑口吻；"
            "阴阳怪气那条要假客气真刺痛。\n"
        )
    if any("暧昧" in m["name"] for m in modes_use):
        user_prompt += (
            "其中名称含「暧昧」的那条要更亲昵、有试探感，"
            "可用省略号和轻声反问，点到为止。\n"
        )
    if any("阴阳" in m["name"] for m in modes_use):
        user_prompt += (
            "其中名称含「阴阳」的那条必须阴阳感极强：假客气、冷嘲、反问刺痛、假笑，"
            "读完能感到刺痛；绝不能写成普通吐槽、搞笑或正常朋友口吻。\n"
        )
    user_prompt += f"\n对话内容：\n{incoming_use.strip()}"
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.9 if soft else 0.95,
        "messages": [
            {"role": "system", "content": _system_prompt(relation, style_use, modes_use)},
            {"role": "user", "content": user_prompt},
        ],
    }
    base = api_base.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    last_http: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if _is_content_filter(detail):
                raise ContentFilterError(_friendly_http_error(e.code, detail)) from e
            msg = _friendly_http_error(e.code, detail)
            if _is_transient(e.code, detail) and attempt < 2:
                last_http = TransientAPIError(msg)
                time.sleep(0.8 * (attempt + 1))
                continue
            if _is_transient(e.code, detail):
                raise TransientAPIError(msg) from e
            raise RuntimeError(msg) from e
        except urllib.error.URLError as e:
            msg = f"无法连接 API: {e.reason}"
            if attempt < 2:
                last_http = TransientAPIError(msg)
                time.sleep(0.8 * (attempt + 1))
                continue
            raise TransientAPIError(msg) from e
    else:
        assert last_http is not None
        raise last_http

    data = json.loads(raw)
    err = (data.get("error") or {}) if isinstance(data, dict) else {}
    if isinstance(err, dict) and err:
        blob = json.dumps(data, ensure_ascii=False)
        if str(err.get("code")) == "1301" or _is_content_filter(blob):
            raise ContentFilterError(_friendly_http_error(400, blob))
        if _is_transient(500, blob):
            raise TransientAPIError(_friendly_http_error(500, blob))

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"模型返回格式异常：{str(data)[:200]}") from e
    if not isinstance(content, str):
        content = str(content)
    replies = _extract_json_list(content) or _parse_lines(content, n)
    if not replies:
        raise RuntimeError(f"模型没有返回可用回复：{content[:200]}")
    while len(replies) < n:
        replies.append(replies[-1])
    return [_with_prefix(r, reply_prefix) for r in replies[:n]]


def generate_replies(
    incoming: str,
    *,
    api_base: str,
    api_key: str,
    model: str,
    style: str = "",
    relation: str = "平辈",
    modes: list[dict[str, str]] | None = None,
    reply_prefix: str = "自动：",
    count: int = 3,
    timeout: float = 45.0,
    fallback_models: list[str] | None = None,
) -> list[str]:
    del count  # length follows resolved modes (3–4)
    if not incoming.strip():
        raise ValueError("对方消息为空")
    if not api_key or "填这里" in api_key:
        raise ValueError("请先在 config.json 里填写 api_key")
    if relation not in RELATION_STYLES:
        relation = "平辈"
    resolved = resolve_modes(relation, modes)

    models: list[str] = []
    for name in [model, *(fallback_models or DEFAULT_FALLBACKS)]:
        if name and name not in models:
            models.append(name)

    last_err: Exception | None = None
    for i, name in enumerate(models):
        for soft in (False, True):
            try:
                return _chat_once(
                    incoming=incoming,
                    api_base=api_base,
                    api_key=api_key,
                    model=name,
                    relation=relation,
                    style=style,
                    modes=resolved,
                    reply_prefix=reply_prefix,
                    timeout=timeout,
                    soft=soft,
                )
            except ContentFilterError as e:
                last_err = e
                if not soft:
                    time.sleep(0.3)
                    continue  # retry same model with softer prompts
                break  # try next model
            except TransientAPIError as e:
                last_err = e
                if i < len(models) - 1:
                    time.sleep(0.8)
                    break  # next fallback model
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                busy = "忙" in msg or "限流" in msg or "429" in msg
                if busy and i < len(models) - 1:
                    time.sleep(0.6)
                    break
                if i < len(models) - 1 and "无法连接" in msg:
                    time.sleep(0.4)
                    break
                raise
    assert last_err is not None
    raise last_err
