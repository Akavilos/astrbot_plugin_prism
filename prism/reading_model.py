"""调用模型理解当前互动，并解析目标触动、语气和关系证据。
结果不可解析时使用规则读取，关系延续与当前判断在此合并。"""


from __future__ import annotations

from . import runtime as runtime_mod

import json
import re

from astrbot.api import logger

from .goals import EVENTS, GoalEvent, Relation, Touch
from .reading import RelationRead, ReadResult


_SYSTEM = """我在读一段聊天记录，判断「刚收到的这句话」对我来说碰到了什么。

我只输出一个 JSON 对象，不加 markdown 围栏和解释。

字段：
{
  "belonging":  -1.0~1.0,
  "esteem":     -1.0~1.0,
  "autonomy":   -1.0~1.0,
  "safety":     -1.0~1.0,
  "novelty":    -1.0~1.0,
  "fairness":   -1.0~1.0,
  "no_harm":    -1.0~1.0,
  "competence": -1.0~1.0,
  "aesthetic":  -1.0~1.0,
  "confidence": 0.0~1.0（我对判断的确信度）,
  "closeness": 0.0~1.0（我与当前说话人的亲近程度）,
  "attraction": -1.0~1.0（我对当前说话人有没有那层意思；读不出就给 0）,
  "history":    0.0~1.0（我与这个人的相处经历）,
  "agency":     "self" | "other" | "circumstance"（这件事是谁造成的）,
  "topic":      "这段话在聊的**话题中心**，3~8 个字（如"夏日祭活动"、"等人上线"）",
  "tone":       "轻松" | "中性" | "沉重"（**话题本身的轻重**，与好坏无关）,
  "evidence":   ["我依据的原文片段，最多3条"],
  "relationship": {
    "actor": "sender 或 other 或 unknown",
    "liking": {"score": -1.0~1.0, "evidence": ["本轮原话"], "context_evidence": ["支持判断的历史原话，没有则为空"]},
    "reliability": {"score": -1.0~1.0, "evidence": ["本轮原话"], "context_evidence": ["支持判断的历史原话，没有则为空"]},
    "honesty": {"score": -1.0~1.0, "evidence": ["本轮原话"], "context_evidence": ["支持判断的历史原话，没有则为空"]},
    "benevolence": {"score": -1.0~1.0, "evidence": ["本轮原话"], "context_evidence": ["支持判断的历史原话，没有则为空"]}
  }
}

我把九个情绪目标平铺在最外层，只有 relationship 是单独的对人评价。

═══ 我对这个人的评价，与我的即时情绪分开 ═══
我只评价本轮实际发言人的行为，actor=sender。谈论第三人、转述新闻、引用别人的话，不算当前发言人的品行。
liking：我是否因这个人的互动而更喜欢或更不喜欢与他相处；不是我是否喜欢话题或作品。
reliability：他确实守诺、履行约定、承担责任为正；有明确依据的失约或推诿为负。新鲜、意外、普通聊天不说明可靠性。
honesty：有依据的坦诚、承认并纠正错误为正；有依据的欺骗、隐瞒为负。观点分歧、疑问、玩笑、我自己记错，都不代表他不诚实。
benevolence：他对我具体的关心、帮助、尊重边界为正；明确的伤害、恶意、利用为负。出于关心的提醒不自动算恶意。
我无法从本轮判断的维度给 0，证据为空。中性寒暄不必加分；我聊得多、我为他做过什么，也不是他的品行证据。
我给非零评分时，evidence 必须逐字摘自刚收到的这句话；历史只帮助理解，不能把旧事当作本轮又发生了一次。
我在 context_evidence 中只摘录历史中与本轮判断直接相关的原话，不抄无关内容来凑依据。[同一发言人]是当前这个人以前说的话，[我]是我说的话，[其他人]不能算到他头上。
我判断的是这轮带来的正负变化，score不是他的总体信任分。没有新增迹象时给0；重复提起旧行为不代表又履约或又失约。
我把谈吐中的坦诚、尊重、善意视为可以逐渐建立信任的线索，但表达流畅、夸奖我、意见相同、身份高低并不等于可靠。生硬措辞、合理拒绝和意见不同也不等于恶意。
我只表达这轮如何影响我的判断，不决定更新比例；证据权重与长期累积由代码计算。
我不把“我保证”“相信我”等自我宣称当成已经兑现的可靠性，也不因他没接我的话题就降低信任。

═══ `touches` 是这次读心的核心 ═══

我分别判断这句话碰到了哪些目标，以及对我影响的方向和强度；没有碰到的目标给 0：

    正数 = 满足 / 靠近了这个目标     负数 = 违背 / 远离了这个目标
      0  = 这句话**没有碰到**它

九个目标分别是什么：

- belonging   被爱、被在乎      （被惦记→正；被冷落、被当空气→负）
- esteem      自尊、被认可      （被夸、被认真对待→正；被贬低、被无视→负）
- autonomy    自主、不被支配    （由着我→正；被使唤、被逼、被查岗→负）
- safety      安全、免于威胁    （踏实→正；被吓到、感到危险→负）
- novelty     新鲜、好奇        （没见过的东西→正；陈词滥调、重复→负）
- fairness    公平、被公正对待  （讲道理→正；被冤枉、被双标→负）
- no_harm     不伤害他人、守诺  （我做了对得起人的事→正；我辜负了谁→负）
- competence  胜任、把事情做好  （聊得明白、接得住→正；卡壳、被问倒→负）
- aesthetic   美、趣味          （**谈论喜欢的东西、有味道的观点、合口味的作品→正**；
                                 无趣、俗气、扫兴→负）

🔴 **三条最容易读错的，重点看**：

1. **聊天聊得投入，是有份量的。**
   聊一部两个人都看过的东西、交换看法、被对方的话触动 ——
   这些碰的是 `aesthetic` 和 `competence`，**不是零**。
   我不因它只是闲聊就忽略真实的触动。

2. **认真讨论一个沉重的话题，碰的是 `competence` 和 `aesthetic`。**
   （"人在知道自己会失败的前提下还去做，是不是白费"这种）
   我在意这话有没有说到点上、有没有接住，不必把讨论理解成输赢。

3. **疑问句不是陈述句。**
   我把"有人给你表白过吗"理解为一个问题，不当成说话人在表白，也不单凭它是问题就判定我的自主受干涉。
   我结合这轮语气和已有往来感受它；说话人自己表达喜欢时，我才据此感受被喜欢。

我也留意语气：赌气、催促、撒娇、亲昵称呼可能影响我的感受；没有直接的情绪词不代表没有感受。

我按实际分量给强度，轻微触动可以接近 0，明显触动才给较大值。我不为每句话凑情绪变化，也不把重复、短句或没有接话本身视为冷落。
我会把问候、吃饭与近况的询问、顺口回问当作普通往来的可能解释；追问、逼迫、威胁或已经明确拒绝后继续施压，才是另一种情境。我仍可以有自己的敏感与误会，这些只是我的这次感受。

═══ `tone` 单独说 ═══

我分开看目标与 tone：目标是对我的影响，tone 是话题本身的轻重。

- 轻松：闲聊、开玩笑、分享喜欢的东西、报个平安
- 中性：普通交代事情、问个路、约个时间
- 沉重：讨论生死/失败/离别/背叛这类有分量的题目；
        哪怕是**聊得很投机**的沉重话题，也是"沉重"——投机影响目标，
        沉不沉是另一回事。"""


def _build_user_msg(history: list[str], text: str) -> str:
    hist = "\n".join(history[-8:]) if history else "（没有更早的记录）"
    return (f"【最近的对话】\n{hist}\n\n"
            f"【刚收到的这句话】\n{text}\n\n"
            f"我根据以上记录判断刚收到的这句话。")


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_read(raw: str) -> dict | None:
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
            pass


    frag = m.group(0) if m else s[s.find("{"):]
    if frag:
        for _ in range(3):
            try:
                d = json.loads(frag)
                return d if isinstance(d, dict) else None
            except Exception:
                frag += "}"
    return None


GOAL_KEYS = ("belonging", "esteem", "autonomy", "safety", "novelty",
             "fairness", "no_harm", "competence", "aesthetic")


def _touches_to_event(d: dict) -> GoalEvent:
    raw = d.get("touches") if isinstance(d.get("touches"), dict) else d
    touches: list[Touch] = []
    for g in GOAL_KEYS:
        try:
            v = float(raw.get(g, 0.0))
        except (TypeError, ValueError):
            v = 0.0
        v = max(-1.0, min(1.0, v))

        if abs(v) >= 0.05:
            touches.append(Touch(goal=g, direction=v, note="模型直读"))
    if not touches:
        touches = [Touch(goal="belonging", direction=0.1, note="无显著碰触")]


    try:
        conf = float(d.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    base_novelty = max(0.0, min(1.0, 0.5 - 0.3 * conf))

    agency = str(d.get("agency") or "other").strip()
    if agency not in ("self", "other", "circumstance"):
        agency = "other"

    return GoalEvent(name="(直读)", touches=tuple(touches),
                     base_novelty=base_novelty, agency=agency)


def to_read_result(text: str, d: dict, history: list[str] | None = None) -> ReadResult:
    if any(k in d for k in GOAL_KEYS) or isinstance(d.get("touches"), dict):
        event = _touches_to_event(d)
    else:
        name = str(d.get("event") or "smalltalk").strip()
        if name not in EVENTS:
            logger.debug("[prism] 模型给出未知事件 %r，降级为 smalltalk", name)
            name = "smalltalk"
        event = EVENTS[name]

    def _num(key: str, lo: float, hi: float, default: float) -> float:
        try:
            return max(lo, min(hi, float(d.get(key, default))))
        except (TypeError, ValueError):
            return default

    rel = RelationRead(
        closeness=_num("closeness", 0.0, 1.0, 0.3),
        attraction=_num("attraction", -1.0, 1.0, 0.0),
        history=_num("history", 0.0, 1.0, 0.3),
        trust=0.5,
        evidence=[str(x)[:40] for x in (d.get("evidence") or [])][:3],
    )
    tone = str(d.get("tone") or "").strip()
    if tone not in ("轻松", "中性", "沉重"):
        tone = ""

    from .relation_evidence import read_relationship
    return ReadResult(
        text=text,
        event=event,
        tone=tone,
        event_confidence=_num("confidence", 0.0, 1.0, 0.5),
        relation=rel,
        topic=str(d.get("topic") or "").strip()[:12],
        relationship=read_relationship(text, {**d, "confidence": _num("confidence", 0.0, 1.0, 0.5)}, history),
    )


async def read_async(provider, text: str, history: list[str] | None = None,
                     base_relation: RelationRead | None = None) -> ReadResult:
    """请求模型并解析互动理解；失败走规则读取，成功时合并已有关系背景。"""
    from .reading import read as read_rule

    if not text or not text.strip():
        return read_rule(text, history, base_relation)

    try:
        resp = await runtime_mod.call_model(provider,
            prompt=_build_user_msg(history or [], text),
            system_prompt=_SYSTEM,
            temperature=0.2,


            max_tokens=20000,
        )
        raw = getattr(resp, "completion_text", "") or ""
        d = parse_read(raw)
        if not d:
            logger.warning("[prism] 模型读心输出无法解析，降级用规则。原文前 120 字：%s", raw[:120])
            return read_rule(text, history, base_relation)
        result = to_read_result(text, d, history)
    except Exception as error:
        runtime_mod.record_failure("model", error)
        logger.exception("[prism] 模型读心失败，降级用规则")
        return read_rule(text, history, base_relation)


    if base_relation is not None:
        b = base_relation
        result.relation.closeness = round(0.6 * result.relation.closeness
                                          + 0.4 * b.closeness, 3)
        result.relation.history = round(max(result.relation.history, b.history), 3)
        if b.attraction != 0.0:


            result.relation.attraction = b.attraction
    return result
