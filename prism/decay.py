"""维护记忆强度、回想加固、再巩固记录和概括条件。
记忆痕迹使用指数衰减；独立的情绪强度衰减函数位于affect模块。"""


from __future__ import annotations

from .runtime import elapsed_since

import math
import time
from dataclasses import dataclass, field


HALF_LIFE_HOURS = 72.0


AROUSAL_PROTECTION = 0.40


SALIENCE_PROTECTION = 0.35


REHEARSAL_BOOST = 0.35


REHEARSAL_HALF_LIFE_BOOST = 0.18


RECONSOLIDATION_WINDOW = 6 * 3600


FORGOTTEN = 0.02


@dataclass
class Trace:
    key: str
    strength: float = 1.0
    encoded_at: float = 0.0
    arousal_at_encode: float = 0.0
    salience: float = 0.0
    rehearsals: int = 0
    last_recalled: float = 0.0
    _cache_strength: float = field(default=0.0, repr=False)

    def current(self, now: float | None = None) -> float:
        """按有效半衰期指数衰减，并计入回想次数的加固系数。"""
        now = now or time.time()
        elapsed = elapsed_since(self.encoded_at, now)
        if elapsed < 60:
            return self.strength


        protect = (AROUSAL_PROTECTION * min(1.0, abs(self.arousal_at_encode))
                   + SALIENCE_PROTECTION * min(1.0, self.salience * 3))
        half_life = HALF_LIFE_HOURS * (1.0 + 3.0 * min(1.0, protect))

        half_life *= (1.0 + REHEARSAL_HALF_LIFE_BOOST * self.rehearsals)

        hours = elapsed / 3600.0
        base = self.strength * (0.5 ** (hours / half_life))
        return base * (1.0 + REHEARSAL_BOOST * self.rehearsals)

    def forgotten(self, now: float | None = None) -> bool:
        return self.current(now) < FORGOTTEN

    def recall(self, now: float | None = None) -> None:
        """登记一次回想，更新加固信息与再巩固时间窗口。"""
        self.rehearsals += 1
        self.last_recalled = now or time.time()

    def consolidating(self, now: float | None = None) -> bool:
        if not self.last_recalled:
            return False
        return elapsed_since(self.last_recalled, now) <= RECONSOLIDATION_WINDOW


@dataclass
class Revision:
    key: str
    before: str
    after: str
    reason: str
    at: float = 0.0

    def explain(self) -> str:
        return f"[{self.key}] {self.before[:24]} → {self.after[:24]}（{self.reason}）"


def reconsolidate(trace: Trace, scene, new_reading: str, reason: str,
                  now: float | None = None) -> Revision | None:
    now = now or time.time()
    if not trace.consolidating(now):
        return None

    before = getattr(scene, "narrative", "") or ""
    if before.strip() == new_reading.strip():
        return None

    rev = Revision(key=trace.key, before=before, after=new_reading,
                   reason=reason, at=now)


    revs = getattr(scene, "revisions", None)
    if isinstance(revs, list):
        revs.append(rev)


    if hasattr(scene, "narrative"):
        scene.narrative = new_reading

    trace.strength = min(2.0, trace.strength * 1.05)
    return rev


@dataclass
class Generalization:
    theme: str
    scene_keys: list[str] = field(default_factory=list)
    count: int = 0
    first_at: float = 0.0
    last_at: float = 0.0

    def explain(self) -> str:
        return f"「{self.theme}」（{self.count} 次，{self.scene_keys[:3]}…）"


def is_generalizable(scenes: list, *, min_count: int = 3,
                     max_avg_salience: float = 0.06) -> bool:
    if len(scenes) < min_count:
        return False
    sals = [getattr(s, "peak_salience", 0.0) for s in scenes]
    return (sum(sals) / len(sals)) <= max_avg_salience


class MemoryStore:
    def __init__(self) -> None:
        self.traces: dict[str, Trace] = {}
        self.scenes: dict[str, object] = {}

    def add(self, scene, now: float | None = None) -> Trace:
        key = f"scene:{scene.scene_id}"
        t = Trace(
            key=key,


            strength=1.0,
            encoded_at=now or getattr(scene, "start", time.time()),
            arousal_at_encode=getattr(getattr(scene, "peak_mood", None), "a", 0.0),
            salience=getattr(scene, "peak_salience", 0.0),
        )
        self.traces[key] = t
        self.scenes[key] = scene
        return t

    def alive(self, now: float | None = None) -> list[str]:
        return [k for k, t in self.traces.items() if not t.forgotten(now)]

    def recall(self, keys: list[str], now: float | None = None) -> list[str]:
        got = []
        for k in keys:
            t = self.traces.get(k)
            if t and not t.forgotten(now):
                t.recall(now)
                got.append(k)
        return got

    def review(self, now: float | None = None) -> dict:
        now = now or time.time()
        alive = [k for k, t in self.traces.items() if not t.forgotten(now)]
        reconsolidating = [k for k, t in self.traces.items() if t.consolidating(now)]
        return {
            "total": len(self.traces),
            "alive": len(alive),
            "forgotten": len(self.traces) - len(alive),
            "reconsolidating": len(reconsolidating),
        }
