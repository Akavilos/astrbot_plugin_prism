"""将经历整理为主观回忆文字，解析模型结果并更新场景叙事字段。"""


from __future__ import annotations

from . import runtime as runtime_mod

import json
import re

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("prism")

from .scene import Scene


_SYSTEM = """我在回想自己刚刚经历过的一段时间。

我看到这段时间的往来记录和自己当时的状态，记下我感知到的那个版本。
我只输出一个 JSON 对象，不加 markdown 围栏和解释。

{
  "narrative": "我对这段时间的回忆，两三句到一小段",
  "doing": "我那时在做什么（一句话）",
  "noticed": ["我当时注意到的事，最多3条"],
  "not_noticed": ["记录里发生过、但我当时没留意的事，没有就空数组"],
  "about_others": {"实际人物名字": "我当时对这个人的感觉（一句话）"},
  "meaning": "这段经历对我的意义，没有就空字符串"
}

🔴 写作要求：

我用第一人称记自己的回忆。

我保留当时的理解，不替对方断定真正的意思。我的误会也是那次经历的一部分；后来明确收到的纠正，我与最初的理解分别记下。

**但"我当时是这么理解的"和"事情就是这样"，是两回事 —— 要分开。**
我保留自己的感受和想法。我的判断、纠正和评价仍写成“我觉得”或“我以为”；我实际说过什么以往来记录为依据，判断的内容不因此成为客观事实。

    反例（把判断写成了事实）：
      "他还把他们的关系记岔了，我提醒了他一句。"
      我若这样记，就把当时的判断误记成了事实。

    正例（判断留在"我以为"里）：
      "我听他那话，觉得他是记岔了，就这么跟他说了一句。"
      我保留了当时的看法，也保留它只是看法的性质。

我在日记里打算提醒、纠正或联系某人，不代表已经说出口。记录有实际发言时，我可以记下自己说过；对方是否真的错了仍是另一个问题。

我只在 narrative 里回想当时感知到的事。记录中我未留意的部分放入 not_noticed，不混进我的回忆。

我也有平淡的回忆，不为一段经历添转折、冲突或特别意义。

我记下自己的经历，不逐句背诵记录。人物和事情各自有出处，我不把不同人的话合到一个人身上。"""


def _build_user_msg(sc: Scene, her_prose: list[str] | None = None) -> str:
    import datetime

    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")

    lines = []
    for m in sc.moments[:80]:
        who = m.speaker or "（对方）"
        if m.is_hers:
            lines.append(f"[{fmt(m.ts)}] 我：{m.text}")
        else:
            lines.append(f"[{fmt(m.ts)}] {who}：{m.text}")
    if len(sc.moments) > 80:
        lines.append(f"…（另有 {len(sc.moments)-80} 条，略）")

    emo = "、".join(f"{k}{v:.2f}" for k, v in (sc.peak_emotions or ())) or "没有明显情绪"
    parts = [
        f"【时间】{fmt(sc.start)} — {fmt(sc.end)}",
        "【这段时间的往来】\n" + "\n".join(lines),
        "\n【我当时的状态】",
        f"· 这段时间里我心情最被牵动的一刻：{sc.peak_mood}",
        f"· 那一刻的情绪：{emo}",
        f"· 牵动我的那句话：{sc.peak_text or '（没有特别的一句）'}",
    ]
    if her_prose:
        parts.append("【我当时写下的内心戏】\n" + "\n".join(f"· {p}" for p in her_prose[:3]))
    parts.append("\n我从这些经历里记下自己的回忆。")
    return "\n".join(parts)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_scene(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except Exception:
        pass
    m = _JSON_RE.search(s)
    if m:
        try:
            d = json.loads(m.group(0))
            return d if isinstance(d, dict) else None
        except Exception:
            return None
    return None


def apply_scene(sc: Scene, d: dict) -> Scene:
    sc.narrative = str(d.get("narrative") or "").strip()
    sc.doing = str(d.get("doing") or "").strip()
    sc.noticed = [str(x)[:60] for x in (d.get("noticed") or [])][:3]
    sc.not_noticed = [str(x)[:60] for x in (d.get("not_noticed") or [])][:3]
    sc.meaning = str(d.get("meaning") or "").strip()[:80]

    about = d.get("about_others")
    if isinstance(about, dict):
        sc.about_others = {str(k)[:20]: str(v)[:80] for k, v in list(about.items())[:8]}
    return sc


async def write_scene(provider, sc: Scene, her_prose: list[str] | None = None,
                      *, max_tokens: int = 20000) -> Scene:
    if not sc.moments:
        return sc
    try:
        resp = await runtime_mod.call_model(provider,
            prompt=_build_user_msg(sc, her_prose),
            system_prompt=_SYSTEM,
            temperature=0.7,


            max_tokens=max_tokens,
        )
        raw = getattr(resp, "completion_text", "") or ""
        d = parse_scene(raw)
        if not d:
            logger.warning("[prism] 场景叙事无法解析，留空。前 120 字：%s", raw[:120])
            return sc
        return apply_scene(sc, d)
    except Exception as error:
        runtime_mod.record_failure("model", error)
        logger.exception("[prism] 写场景失败，留空叙事")
        return sc
