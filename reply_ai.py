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
        "【身份：长辈】对方是父母/长亲/老师等，辈分更高。\n"
        "你的口吻 = 晚辈回话：稳重、礼貌，优先用「您」；短句清楚，不顶嘴。\n"
        "身份辨识标准：读完应像在跟爸妈/老师发微信，绝不像损友互怼。\n"
        "严禁：暧昧撩拨、阴阳怪气、网络烂梗、油腻奉承、顶撞、复读对方原词。\n"
        "三条差异只在「稳妥 / 关心 / 轻松」，整体仍必须是对长辈。"
    ),
    "平辈": (
        "【身份：平辈】对方是朋友/同学/同事/恋人等，同辈。\n"
        "你的口吻 = 同龄人微信：口语、短句、有态度，可用「你」。\n"
        "身份辨识标准：读完应像损友/暧昧对象聊天，绝不「您」或客服腔。\n"
        "四条必须一眼能分清：正常≠暧昧≠搞笑≠阴阳；禁止四条同质化、禁止复读对方原句。\n"
        "严禁写成对长辈的客服腔或对晚辈的说教腔。"
    ),
    "晚辈": (
        "【身份：晚辈】对方更年轻（弟弟妹妹/学生等）。\n"
        "你的口吻 = 靠谱的大一点同辈：亲切平等、可带一点照顾，但不端着训人。\n"
        "身份辨识标准：读完应像对弟弟妹妹说话，不油腻不爹味。\n"
        "严禁：暧昧、阴阳怪气、爹味说教、空洞「加油你一定行」、复读对方原词。\n"
        "三条差异在「随和 / 耐心 / 幽默」，整体仍像对年轻人。"
    ),
}

STYLE_GUIDES: dict[str, dict[str, str]] = {
    "长辈": {
        "稳妥得体": "先把事情说清楚；句子完整；少语气词；不用表情。像汇报/应答，不撒娇。",
        "关心体贴": "先接住对方的事，再补一句具体关心（吃饭/休息/别着凉）；温柔克制，不肉麻。",
        "轻松自然": "仍尊敬，但更短更口语；最多一个「嗯嗯/哈哈」；绝不玩笑过头。",
    },
    "平辈": {
        "正常风格": (
            "普通朋友随口回：直接接话、给态度或下一步，信息清楚；"
            "0-1个语气词；几乎不用表情；不要撩、不要阴阳、不要硬搞笑；"
            "禁止复读对方原词（如对方说随缘，不要回「随缘吧」）。"
        ),
        "暧昧风格": (
            "必须像在撩/拉近距离：亲昵、试探、轻吃醋、想见或「就你这样…」；"
            "可用「你啊」「嗯？」「笨蛋」和省略号；1-2个软表情😊🥰😳；"
            "读完必须能听出暧昧；禁止写成普通朋友、搞笑吐槽或阴阳。"
        ),
        "搞笑风格": (
            "必须好笑：接梗、反转、夸张吐槽、自嘲；可夸张改编对方原意，不要原句复读；"
            "1个逗趣表情😂🤣👀；禁止假客气阴阳，禁止油腻暧昧。"
        ),
        "阴阳怪气": (
            "必须假客气真刺痛：表面礼貌关心，实际冷嘲挖苦；"
            "句式如「挺好的呀」「您开心就好」「那你继续呗」「哦？懂了」；"
            "可叠「哈/呵/呢」；可带假笑😊或👀；"
            "禁止脏话；禁止写成普通吐槽或搞笑段子——要阴阳。"
        ),
    },
    "晚辈": {
        "随和自然": "平等口语，像熟人；简短直接；不端着。",
        "耐心回应": "把对方没说清的点讲明白或给可行下一步；温和，不说教、不鸡汤。",
        "轻松幽默": "温和俏皮接话；可轻调侃但不贬低；表情最多1个。",
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
        f"【人设底色】（可参考，但不得抹平身份与风格差异）：{persona}\n"
        if persona.strip()
        else "未指定专属人设：严格按身份 + 各风格生成。\n"
    )
    contrast = {
        "长辈": "例：对方「今天加班」→ 稳妥「好的，您注意身体」/ 关心「加班辛苦了，记得吃饭」/ 轻松「嗯嗯，别太累」——都像对长辈。",
        "平辈": "例：对方「随缘吧」→ 正常「行，那先这样」/ 暧昧「你这么说…我有点想你了」/ 搞笑「行吧社畜佛系冠军」/ 阴阳「挺好的呀，随缘真厉害呢」——四条气质完全不同。",
        "晚辈": "例：对方「好难啊」→ 随和「怎么了，说说」/ 耐心「先把哪一步卡住了发我」/ 幽默「难度拉满是吧」——像对年轻人，不爹味。",
    }.get(relation, "")
    return f"""你在代替「用户」发微信。你写的每一句 = 「自己」发出去的话。

【角色铁律】
- 「对方」= 聊天对象刚说的/历史里对方说的
- 「自己」= 用户已经发过的
- 你只写「自己」的下一句，去接「对方」最新内容
- 禁止模仿对方口吻；禁止把「自己」历史原句稍改再发
- 禁止输出「对方：」「自己：」前缀或风格名称

【身份铁律】当前对方身份 = {relation}
{rules}
{contrast}

{persona_block}
请按顺序生成 {n} 条，风格必须分明（同一意思也不许句式雷同）：
{guide_lines}

【质量】
- 先看清对方最后一句在说什么，再针对性回，禁止答非所问
- 严禁复读/拼接对方原句当回复（坏例子：对方「随缘吧」→「那确实，随缘吧」）
- 错误示范：对方「随缘吧」→「加油你一定行」/「随缘吧」/四条都差不多
- 正确：接住情绪，{relation}身份下按各风格给出完全不同态度
- 换身份必须换口吻：长辈用您/稳；平辈用你/冲或撩；晚辈亲切不爹
- 口语短句；不要书面语、客服腔、解释、引号、风格名
- 只输出 JSON 字符串数组，长度必须是 {n}
- 简体中文
"""


DEFAULT_FALLBACKS = [
    "glm-4-flash-250414",
    "glm-4-flash",
]

STYLE_LABELS = RELATION_STYLES["平辈"]


def _normalize_reply_items(data: list[Any]) -> list[str] | None:
    """Flatten model quirks: nested lists, Chinese顿号 arrays, quoted blobs."""
    if len(data) == 1 and isinstance(data[0], list):
        data = data[0]
    if len(data) == 1 and isinstance(data[0], str):
        inner = data[0].strip()
        # whole array stuffed into one string, often with 、 instead of ,
        if inner.startswith("[") and inner.endswith("]"):
            fixed = inner.replace("、", ",")
            try:
                nested = json.loads(fixed)
                if isinstance(nested, list) and len(nested) >= 2:
                    data = nested
            except json.JSONDecodeError:
                parts = re.findall(r"[「\"“](.+?)[」\"”]", inner)
                if len(parts) >= 2:
                    data = parts
                else:
                    parts = [p.strip() for p in re.split(r"[、,]", inner.strip("[]")) if p.strip()]
                    if len(parts) >= 2:
                        data = parts
    out = [str(x).strip() for x in data if str(x).strip()]
    return out or None


def _extract_json_list(text: str) -> list[str] | None:
    text = text.strip()
    # model sometimes uses Chinese顿号 inside JSON-like arrays
    candidates = [text, text.replace("、", ",")]
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, list):
                return _normalize_reply_items(data)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        blob = match.group(0)
        for cand in (blob, blob.replace("、", ",")):
            try:
                data = json.loads(cand)
                if isinstance(data, list):
                    return _normalize_reply_items(data)
            except json.JSONDecodeError:
                continue
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
    style_lines = "\n".join(f"{i+1}. {m['name']}：{m['guide']}" for i, m in enumerate(modes_use))
    roast = any(k in style_use for k in ("吐槽", "怼", "损友", "抬杠"))
    example = json.dumps([f"回复{i+1}" for i in range(n)], ensure_ascii=False)
    # Pull last 对方 line for explicit focus
    last_other = ""
    for line in reversed(incoming_use.splitlines()):
        s = line.strip()
        if s.startswith("对方：") or s.startswith("对方:"):
            last_other = re.sub(r"^对方[:：]\s*", "", s).strip()
            break
    user_prompt = (
        f"【对方身份】{relation} ← 全部{n}条都必须符合这个身份的说话分寸\n"
        f"【人设底色】{style_use or '无'}（底色而已；身份与风格差异更重要）\n"
        f"{context_hint}\n"
    )
    if last_other:
        user_prompt += f"【对方最新一句】{last_other}\n你要接的是这句，不要答非所问，不要复读。\n"
    user_prompt += (
        f"按顺序生成 {n} 条（{label_join}），气质必须互不相同：\n{style_lines}\n"
        "规则：只写「自己」要发的话；不要角色前缀；不要风格标签；不要脏话违法内容。\n"
        f"返回 JSON 数组，长度 {n}，如 {example}。\n"
    )
    if relation == "长辈":
        user_prompt += (
            "硬性：身份=长辈。三条都要用晚辈对长辈口吻（可「您」）；"
            "禁止暧昧/阴阳/烂梗/顶撞；禁止复读对方原词。\n"
        )
    elif relation == "晚辈":
        user_prompt += (
            "硬性：身份=晚辈。三条都要亲切平等、不爹味；"
            "禁止暧昧/阴阳；禁止复读对方原词。\n"
        )
    elif relation == "平辈":
        user_prompt += (
            "硬性：身份=平辈。四条必须分别是：正常朋友 / 明显在撩 / 明显好笑 / 明显阴阳。"
            "禁止复读对方原词；禁止四条同质；禁止写成对长辈的「您」。\n"
        )
    if roast and relation == "平辈":
        user_prompt += "人设偏损友：正常也可以冲一点，但暧昧仍要撩、阴阳仍要假客气。\n"
    user_prompt += f"\n完整对话：\n{incoming_use.strip()}"
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.85 if soft else 1.0,
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
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if _is_content_filter(detail):
                raise ContentFilterError(_friendly_http_error(e.code, detail)) from e
            msg = _friendly_http_error(e.code, detail)
            if _is_transient(e.code, detail) and attempt < 1:
                last_http = TransientAPIError(msg)
                time.sleep(0.5)
                continue
            if _is_transient(e.code, detail):
                raise TransientAPIError(msg) from e
            raise RuntimeError(msg) from e
        except urllib.error.URLError as e:
            msg = (
                f"无法连接 API: {e.reason}\n"
                "若需代理，可在系统设置代理，或设置环境变量 HTTPS_PROXY 后重启程序。"
            )
            if attempt < 1:
                last_http = TransientAPIError(msg)
                time.sleep(0.5)
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
    # refuse single-blob / duplicate padding that collapses styles
    if len(replies) == 1 and n > 1:
        raise TransientAPIError(f"模型只返回了1条，需要{n}条风格差异回复")
    if len(replies) < n:
        raise TransientAPIError(f"模型只返回了{len(replies)}条，需要{n}条")
    # drop accidental style labels leaked into text
    cleaned: list[str] = []
    for r in replies[:n]:
        r = re.sub(r"^(稳妥得体|关心体贴|轻松自然|正常风格|暧昧风格|搞笑风格|阴阳怪气|随和自然|耐心回应|轻松幽默)[:：\s]+", "", r).strip()
        cleaned.append(r)
    return [_with_prefix(r, reply_prefix) for r in cleaned]


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
                if not soft:
                    time.sleep(0.3)
                    continue  # same model, softer / second try
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
