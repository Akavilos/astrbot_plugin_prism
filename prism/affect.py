"""表示愉悦、唤醒和主导感坐标，将情绪输入映射为心情变化。
更新返回变化前后状态、位移与归一化显著性。"""


from __future__ import annotations

import math
from dataclasses import dataclass, field, replace


ALPHA = 0.18


BETA = 0.08


PAD_SPAN = 2.0


MIN_INTENSITY = 0.02


MIN_HEADROOM = 0.25


@dataclass(frozen=True)
class Mood:
    p: float = 0.0
    a: float = 0.0
    d: float = 0.0

    def clamped(self) -> "Mood":
        return Mood(
            max(-1.0, min(1.0, self.p)),
            max(-1.0, min(1.0, self.a)),
            max(-1.0, min(1.0, self.d)),
        )

    def distance_to(self, other: "Mood") -> float:
        dp, da, dd = self.p - other.p, self.a - other.a, self.d - other.d
        return math.sqrt(dp * dp + da * da + dd * dd) / (PAD_SPAN * math.sqrt(3))

    def is_zero(self, eps: float = 1e-9) -> bool:
        return abs(self.p) < eps and abs(self.a) < eps and abs(self.d) < eps

    def magnitude(self) -> float:
        return math.sqrt(self.p * self.p + self.a * self.a + self.d * self.d)


EMOTION_TO_PAD: dict[str, tuple[float, float, float]] = {

    "喜悦":  ( 0.40,  0.20,  0.10),
    "悲伤":  (-0.40, -0.20, -0.50),
    "恐惧":  (-0.64,  0.60, -0.43),
    "愤怒":  (-0.51,  0.59,  0.25),
    "厌恶":  (-0.40,  0.20,  0.10),
    "信任":  ( 0.40,  0.20, -0.30),
    "期待":  ( 0.20,  0.20, -0.10),


    "自豪":  ( 0.40,  0.30,  0.30),
    "感激":  ( 0.40,  0.20, -0.30),


    "喜欢":  ( 0.35,  0.15,  0.20),
    "欣慰":  ( 0.30, -0.20,  0.40),


    "惊讶":  ( 0.05,  0.75, -0.20),
}


EXTRA_EMOTIONS: dict[str, tuple[float, float, float]] = {

    "平静":  ( 0.10, -0.55,  0.20),


    "凝重":  (-0.45, -0.30, -0.30),
    "轻快":  ( 0.35, -0.20,  0.35),
    "无聊":  (-0.25, -0.60,  0.00),

    "失望":  (-0.50,  0.10, -0.30),

    "内疚":  (-0.55,  0.15, -0.55),

    "羞耻":  (-0.60,  0.35, -0.85),


    "尴尬":  (-0.40,  0.55, -0.30),
}


@dataclass(frozen=True)
class Emotion:
    kind: str
    intensity: float

    def pad(self) -> Mood | None:
        raw = EMOTION_TO_PAD.get(self.kind) or EXTRA_EMOTIONS.get(self.kind)
        if raw is None:
            return None
        return Mood(*raw)


@dataclass(frozen=True)
class Push:
    before: Mood
    after: Mood
    delta: Mood
    salience: float
    mode: str
    center: Mood
    raw_salience: float = 0.0

    def explain(self) -> str:
        b, a = self.before, self.after
        return (
            f"心情 ({b.p:+.2f},{b.a:+.2f},{b.d:+.2f}) → ({a.p:+.2f},{a.a:+.2f},{a.d:+.2f})  "
            f"| 位移 {self.salience:.3f}  [{self.mode}]"
        )


TONE_GATE: dict[str, dict[str, float]] = {
    "沉重": {
        "喜悦": 0.45,
        "期待": 0.70,
        "轻快": 0.0,
        "平静": 0.60,
    },
    "轻松": {
        "悲伤": 0.70,
        "愤怒": 0.60,
        "凝重": 0.0,
    },
}


def tone_gate(tone: str, kind: str) -> float:
    return TONE_GATE.get(tone or "", {}).get(kind, 1.0)


def emotion_center(emotions: list[Emotion]) -> Mood | None:
    total = 0.0
    sp = sa = sd = 0.0
    for e in emotions:
        pad = e.pad()
        if pad is None or e.intensity <= MIN_INTENSITY:
            continue
        w = e.intensity
        total += w
        sp += w * pad.p
        sa += w * pad.a
        sd += w * pad.d
    if total <= 0:
        return None
    return Mood(sp / total, sa / total, sd / total)


def update(mood: Mood, emotions: list[Emotion], *, alpha: float = ALPHA,
           beta: float = BETA, tone: str = "") -> Push:
    """计算情绪中心；同向且心情模长不小于中心时推深，否则拉向中心。
    显著性按实际位移与剩余坐标空间归一化，无有效中心时保持心情。"""
    center = emotion_center(emotions)
    if center is None:
        return Push(mood, mood, Mood(), 0.0, "none", Mood())


    effective = max(e.intensity for e in emotions
                    if e.pad() is not None and e.intensity > MIN_INTENSITY)
    a_eff = alpha * (0.25 + 0.75 * effective)
    b_eff = beta * (0.25 + 0.75 * effective)


    crossed = False
    if not mood.is_zero() and not center.is_zero():
        dot = mood.p * center.p + mood.a * center.a + mood.d * center.d
        if dot > 0:
            cos = dot / (mood.magnitude() * center.magnitude())
            crossed = cos > 0.8 and mood.magnitude() >= center.magnitude()

    if crossed:

        raw = Mood(
            mood.p + b_eff * (mood.p - center.p),
            mood.a + b_eff * (mood.a - center.a),
            mood.d + b_eff * (mood.d - center.d),
        )
        mode = "push"
    else:

        raw = Mood(
            mood.p + a_eff * (center.p - mood.p),
            mood.a + a_eff * (center.a - mood.a),
            mood.d + a_eff * (center.d - mood.d),
        )
        mode = "pull"

    after = raw.clamped()
    delta = Mood(after.p - mood.p, after.a - mood.a, after.d - mood.d)
    raw_salience = mood.distance_to(after)


    corner = Mood(1.0 if mood.p >= 0 else -1.0,
                  1.0 if mood.a >= 0 else -1.0,
                  1.0 if mood.d >= 0 else -1.0)
    headroom = max(MIN_HEADROOM, mood.distance_to(corner))
    salience = max(0.0, min(1.0, raw_salience / headroom))
    return Push(mood, after, delta, salience, mode, center, raw_salience)


def personality_to_mood(big5: dict[str, float]) -> Mood:
    o = big5.get("openness", 0.5)
    c = big5.get("conscientiousness", 0.5)
    e = big5.get("extraversion", 0.5)
    a = big5.get("agreeableness", 0.5)
    n = big5.get("neuroticism", 0.5)

    o, c, e, a, n = (2 * x - 1 for x in (o, c, e, a, n))
    return Mood(
        0.59 * a + 0.19 * n + 0.21 * e,
        0.57 * n + 0.30 * a + 0.15 * o,
        0.60 * e + 0.32 * a + 0.25 * o + 0.17 * c,
    ).clamped()


def decay(strength: float, elapsed: float, arousal_at_encode: float,
          *, d_base: float = 0.5) -> float:
    """按经过时间与编码唤醒程度计算独立的幂律强度衰减。"""
    if elapsed <= 0:
        return strength

    d = d_base * (1.0 - 0.4 * max(-1.0, min(1.0, arousal_at_encode)))
    d = max(0.05, d)
    return strength / math.pow(elapsed, d)
