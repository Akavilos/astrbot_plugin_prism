"""批量读取带编号的往来，解析场景分段结果并对齐原始消息下标。"""


from __future__ import annotations

from . import runtime as runtime_mod

import json
import re
import time

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("prism")


BATCH_SIZE = 20

_SYSTEM = """我在回看一段聊天记录，按话题分成若干段，记下每段的信息。

我只输出一个 JSON 对象，不加 markdown 围栏和解释。

{
  "segments": [
    {
      "start_index": 0,          // 这一段从第几条开始（0 起）
      "end_index": 3,            // 到第几条结束（含）
      "topic": "这一段在聊什么，3~8 个字",
      "event": "事件名（见下）",
      "confidence": 0.0~1.0,
      "closeness": 0.0~1.0,      // 我与当前说话人的亲近程度
      "attraction": -1.0~1.0,    // 我对这个人有没有那层意思；读不出给 0
      "what": "这一段发生了什么，一句话"
    }
  ]
}

事件名（只能从这些里选）：
confession（表白）· betrayal（欺骗背叛）· threat（让人害怕）
praise（夸奖）· promise_made（给承诺）· promise_broken（承诺没兑现）
correction（指出对方理解错了）· friction（关系张力：赌气/催促/质问/抱怨）
smalltalk（普通闲聊）

🔴 切分规则（这是最重要的）：
我在话题中心变化时分段；话题没变时，我不因消息数量增加而分段。

- 我不把措辞不同等同于换话题；"抽奖结果不好" 和 "奖池有保底" 仍是同一件事。
- 我在从"聊活动"到"聊抽奖"再到"聊谁没来"时分段。
- 我拿不准时合并，保留经历的连续性。

🔴 其他规则：
· **疑问句不是陈述句。** "有人给你表白过吗" 是在**问**，不是表白 → smalltalk。
· 赌气、催促、质问、抱怨对方不配合 → friction，**不是 threat**。
· 我覆盖输入里的每一条记录，输出连续、不重叠的索引。"""


def _fmt(idx: int, kind: str, who: str, text: str) -> str:
    speaker = "我（收信人自己）" if kind == "delivered" else (who or "?")
    return f"[{idx}] {speaker}：{text}"


def _session_label(umo: str, group_name: str = "") -> str:
    if not umo:
        return ""
    if "GroupMessage" in umo:
        return f"群「{group_name}」" if group_name else f"群{umo.split(':')[-1][:6]}"
    return "私聊"


def build_batch_msg(items: list[dict]) -> str:
    lines: list[str] = []
    last_sess = None
    for i, it in enumerate(items):
        sess = it.get("session", "")
        if sess and sess != last_sess:
            lines.append(f"── 以下是{sess}里的对话 ──")
            last_sess = sess
        lines.append(_fmt(i, it.get("kind", "received"), it.get("who", ""), it.get("text", "")))
    return "【聊天记录（编号从 0 开始）】\n" + "\n".join(lines) + \
           "\n\n请把这段记录切成若干段，并读出每一段的信息。"


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_batch(raw: str) -> list[dict] | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    d = None
    try:
        d = json.loads(s)
    except Exception:
        m = _JSON_RE.search(s)
        if m:
            try:
                d = json.loads(m.group(0))
            except Exception:
                d = None
    if not isinstance(d, dict):
        return None
    segs = d.get("segments")
    if not isinstance(segs, list) or not segs:
        return None
    return [x for x in segs if isinstance(x, dict)]


def _num(d: dict, key: str, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(d.get(key, default))))
    except (TypeError, ValueError):
        return default


def to_segments(segs: list[dict], n_items: int) -> list[dict]:
    """约束分段下标并合并覆盖范围，使结果对应当前批次的往来。"""
    from .goals import EVENTS

    out = []
    for s in segs:
        try:
            a = max(0, min(n_items - 1, int(s.get("start_index", 0))))
            b = max(0, min(n_items - 1, int(s.get("end_index", a))))
        except (TypeError, ValueError):
            continue
        if b < a:
            a, b = b, a
        name = str(s.get("event") or "smalltalk").strip()
        if name not in EVENTS:
            name = "smalltalk"
        out.append({
            "start": a, "end": b,
            "topic": str(s.get("topic") or "").strip()[:14],
            "event": name,
            "confidence": _num(s, "confidence", 0.0, 1.0, 0.5),
            "closeness": _num(s, "closeness", 0.0, 1.0, 0.3),
            "attraction": _num(s, "attraction", -1.0, 1.0, 0.0),
            "what": str(s.get("what") or "").strip()[:80],
        })
    if not out:
        return []

    out.sort(key=lambda x: x["start"])


    for i in range(len(out) - 1):
        gap_start = out[i]["end"] + 1
        if out[i + 1]["start"] > gap_start:
            out[i]["end"] = out[i + 1]["start"] - 1
        elif out[i + 1]["start"] <= out[i]["end"]:
            out[i + 1]["start"] = out[i]["end"] + 1
    out[-1]["end"] = n_items - 1


    merged: list[dict] = []
    for x in out:
        if x["end"] < x["start"]:
            if merged:
                merged[-1]["end"] = max(merged[-1]["end"], x["end"])
            continue
        merged.append(x)
    return merged


async def read_batch(provider, items: list[dict], *,
                     batch_size: int = BATCH_SIZE,
                     max_tokens: int = 20000) -> list[dict]:
    """按批次请求理解，再把局部下标转换为整个输入的下标。"""
    if not items:
        return []
    chunks: list[dict] = []
    made = 0
    for off in range(0, len(items), batch_size):
        part = items[off:off + batch_size]
        try:
            resp = await runtime_mod.call_model(provider,
                prompt=build_batch_msg(part),
                system_prompt=_SYSTEM,
                temperature=0.2,


                max_tokens=max_tokens,
            )
            raw = getattr(resp, "completion_text", "") or ""
            segs = parse_batch(raw)
            if not segs:
                logger.warning("[prism] 批量读取无法解析，本批整段处理。前 120 字：%s", raw[:120])
                segs = [{"start_index": 0, "end_index": len(part) - 1,
                         "topic": "", "event": "smalltalk", "what": ""}]
        except Exception as error:
            runtime_mod.record_failure("model", error)
            logger.exception("[prism] 批量读取失败，本批整段处理")
            segs = [{"start_index": 0, "end_index": len(part) - 1,
                     "topic": "", "event": "smalltalk", "what": ""}]

        for s in to_segments(segs, len(part)):
            s["start"] += off
            s["end"] += off
            chunks.append(s)
        made += 1

    return chunks
