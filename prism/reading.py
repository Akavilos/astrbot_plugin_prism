"""提供事件与关系的关键词读取，以及互动理解的数据结构。
模型读取不可用时由调用方选择此规则路径。"""


from __future__ import annotations

from dataclasses import dataclass, field

from .goals import EVENTS, GoalEvent, Relation, Touch


EVENT_CUES: dict[str, tuple[str, ...]] = {
    "confession": ("我喜欢你", "我爱你", "喜欢你", "表白", "爱你", "中意你", "心动"),
    "betrayal": ("骗我", "骗了", "背叛", "出卖", "瞒着", "背着我", "耍我"),
    "threat": ("怕", "恐怖", "吓", "鬼", "危险", "救命", "不敢"),
    "praise": ("厉害", "真棒", "好可爱", "聪明", "好看", "夸"),
    "promise_made": ("答应", "说好", "约好", "一起去", "带我去", "请你"),
    "promise_broken": ("不算数", "没做到", "放鸽子", "失约", "说话不算"),
    "smalltalk": ("在干嘛", "吃了没", "天气", "几点", "睡了吗", "早上好", "晚安"),
}


def detect_event(text: str) -> tuple[str, float]:
    if not text:
        return "smalltalk", 0.0
    best, best_len = "smalltalk", 0
    for name, cues in EVENT_CUES.items():
        for cue in cues:
            if cue in text and len(cue) > best_len:
                best, best_len = name, len(cue)

    conf = min(0.95, 0.30 + 0.16 * best_len) if best_len else 0.15
    return best, conf


@dataclass
class RelationRead:
    closeness: float = 0.3
    attraction: float = 0.0
    history: float = 0.3
    trust: float = 0.5
    evidence: list[str] = field(default_factory=list)

    def to_relation(self) -> Relation:
        return Relation(closeness=self.closeness, attraction=self.attraction,
                        history=self.history, trust=self.trust)


CLOSENESS_CUES: tuple[tuple[str, float, str], ...] = (
    ("小宝", 0.25, "用了很亲昵的称呼"),
    ("亲爱的", 0.20, "用了亲昵的称呼"),
    ("乖", 0.10, "使用宠溺口吻"),
    ("想我", 0.15, "谈及想念"),
    ("别不理", 0.15, "表达对被冷落的在意"),
)


TENSION_CUES: tuple[tuple[str, float, str], ...] = (
    ("算了", -0.10, "对方开始放弃沟通"),
    ("不说就算", -0.15, "对方在收线"),
    ("烦", -0.15, "出现烦字"),
)


PURSUIT_CUES: tuple[str, ...] = ("你直接回答", "到底", "我就想问问", "你说不说", "能不能告诉")


def read_relation(messages: list[str], *, baseline: RelationRead | None = None) -> RelationRead:
    r = baseline or RelationRead()
    text = "\n".join(messages or [])

    for cue, delta, why in CLOSENESS_CUES:
        if cue in text:
            r.closeness = min(1.0, r.closeness + delta)
            r.evidence.append(f"{why}（{cue}）")
    for cue, delta, why in TENSION_CUES:
        if cue in text:
            r.closeness = max(0.0, r.closeness + delta)
            r.evidence.append(f"{why}（{cue}）")


    hits = sum(1 for c in PURSUIT_CUES if c in text)
    if hits >= 2:
        r.closeness = min(1.0, r.closeness + 0.10)
        r.evidence.append(f"反复追问（命中 {hits} 个线索）")

    r.closeness = round(r.closeness, 3)
    return r


@dataclass
class ReadResult:
    text: str
    event: GoalEvent
    event_confidence: float
    relation: RelationRead


    topic: str = ""


    tone: str = ""
    relationship: dict = field(default_factory=dict)

    def explain(self) -> str:
        ev = self.event.name
        rel = self.relation
        evi = "；".join(rel.evidence) or "无"
        return (f"「{self.text[:28]}」→ 事件[{ev}] 确信{self.event_confidence:.2f}  "
                f"关系 亲近{rel.closeness:.2f}/好感{rel.attraction:+.2f}/历史{rel.history:.2f}  "
                f"依据：{evi}")


def read(text: str, history: list[str] | None = None,
         base_relation: RelationRead | None = None) -> ReadResult:
    name, conf = detect_event(text)
    ev = EVENTS.get(name, EVENTS["smalltalk"])
    rel = read_relation((history or []) + [text], baseline=base_relation)
    return ReadResult(text=text, event=ev, event_confidence=conf, relation=rel)


