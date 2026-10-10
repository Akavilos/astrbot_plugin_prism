"""序列化主观心情快照，将坐标转为可读状态并标记过时感受。"""


from __future__ import annotations

from .runtime import elapsed_since

import json
import time
from dataclasses import dataclass, field

from .affect import Mood
from .personality import Personality


STATE_VERSION = 1


STALE_AFTER_SECONDS = 12 * 3600


@dataclass
class MoodState:
    version: int = STATE_VERSION
    mood: Mood = field(default_factory=Mood)
    updated_at: float = 0.0


    last_salience: float = 0.0

    last_emotions: str = ""

    profile: dict = field(default_factory=dict)


    def stale(self, now: float | None = None) -> bool:
        if not self.updated_at:
            return True
        return elapsed_since(self.updated_at, now) > STALE_AFTER_SECONDS

    def describe(self, now: float | None = None) -> str:
        m = self.mood

        line = f"心里{_word_p(m.p)}，眼下{_word_a(m.a)}，{_word_d(m.d)}"


        emo = _strip_scores(self.last_emotions)
        extra = f"——刚被{emo}带了一下" if emo else ""
        note = "（不过这是很久以前的感受了，未必还作数）" if self.stale(now) else ""
        return f"{line}{extra}{note}"

    def to_json(self) -> str:
        return json.dumps({
            "version": self.version,
            "mood": [self.mood.p, self.mood.a, self.mood.d],
            "updated_at": self.updated_at,
            "last_salience": self.last_salience,
            "last_emotions": self.last_emotions,
            "profile": self.profile,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> "MoodState":
        if not raw:
            return cls()
        try:
            d = json.loads(raw)
        except Exception:
            return cls()
        if not isinstance(d, dict):
            return cls()
        try:
            p, a, dd = (d.get("mood") or [0.0, 0.0, 0.0])[:3]
            return cls(
                version=int(d.get("version", STATE_VERSION)),
                mood=Mood(float(p), float(a), float(dd)),
                updated_at=float(d.get("updated_at") or 0.0),
                last_salience=float(d.get("last_salience") or 0.0),
                last_emotions=str(d.get("last_emotions") or ""),
                profile=d.get("profile") if isinstance(d.get("profile"), dict) else {},
            )
        except Exception:
            return cls()


_EMO_SCORE = __import__("re").compile(r"[0-9.]+")


def _strip_scores(text: str) -> str:
    return _EMO_SCORE.sub("", text or "").replace("、、", "、").strip("、 ").strip()


def _word_p(v: float) -> str:
    if v >= 0.5:
        return "很舒畅"
    if v >= 0.15:
        return "还算舒畅"
    if v > -0.15:
        return "没什么特别的感觉"
    if v > -0.5:
        return "有点沉"
    return "很不好受"


def _word_a(v: float) -> str:
    if v >= 0.5:
        return "静不下来"
    if v >= 0.15:
        return "有点起伏"
    if v > -0.15:
        return "没什么起伏"
    if v > -0.5:
        return "有点懒懒的、提不起劲"
    return "沉沉的、不太想动"


def _word_d(v: float) -> str:
    if v >= 0.5:
        return "很有底气"
    if v >= 0.15:
        return "还算有底"
    if v > -0.15:
        return "说不上来有没有底"
    if v > -0.5:
        return "有点没底"
    return "很没底、发虚"


def snapshot(mood: Mood, *, salience: float = 0.0, emotions: str = "",
             personality: Personality | None = None,
             now: float | None = None) -> MoodState:
    profile: dict = {}
    if personality is not None:
        profile = {
            "reactivity": round(personality.reactivity, 3),
            "recovery": round(personality.recovery, 3),
            "expressiveness": round(personality.expressiveness, 3),
            "top_goals": personality.top_goals(4),
            "cares": dict(personality.cares),
        }
    return MoodState(mood=mood, updated_at=now or time.time(),
                     last_salience=salience, last_emotions=emotions,
                     profile=profile)


def read_state(raw: str | None) -> MoodState:
    return MoodState.from_json(raw)
