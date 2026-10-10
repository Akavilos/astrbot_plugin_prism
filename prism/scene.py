"""定义对话瞬间与经历场景，按时间顺序切分并汇总人物、情绪和会话范围。"""


from __future__ import annotations

import time
from dataclasses import dataclass, field

from .affect import Mood


GAP_SECONDS = 15 * 60


MAX_SCENE_SECONDS = 2 * 3600


MIN_SCENE_SALIENCE = 0.01


@dataclass
class Moment:
    ts: float
    speaker: str
    text: str = ""
    is_hers: bool = False


    mood: Mood = field(default_factory=Mood)
    emotions: tuple = ()
    salience: float = 0.0


    umo: str = ""
    event: str = ""
    topic: str = ""


@dataclass
class Scene:
    scene_id: str = ""
    start: float = 0.0
    end: float = 0.0


    present: dict[str, int] = field(default_factory=dict)
    sessions: set[str] = field(default_factory=set)


    moments: list[Moment] = field(default_factory=list)


    peak_salience: float = 0.0
    peak_mood: Mood = field(default_factory=Mood)
    peak_emotions: tuple = ()
    peak_text: str = ""

    mood_at_start: Mood = field(default_factory=Mood)


    felt_about: dict[str, float] = field(default_factory=dict)


    narrative: str = ""

    doing: str = ""

    noticed: list[str] = field(default_factory=list)

    not_noticed: list[str] = field(default_factory=list)

    about_others: dict[str, str] = field(default_factory=dict)

    meaning: str = ""


    revisions: list = field(default_factory=list)

    def has_narrative(self) -> bool:
        return bool(self.narrative.strip())


    public: bool = False

    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def summary_line(self) -> str:
        who = "、".join(k.split(":")[-1] for k in list(self.present)[:4]) or "（无人）"
        dur = self.duration() / 60
        head = f"[{len(self.moments)}刻/{dur:.0f}分] 在场：{who}"
        if self.has_narrative():
            return f"{head}  「{self.narrative[:48]}」"
        return f"{head}  峰值 {self.peak_salience:.3f}" + (
            f"  触点：{self.peak_text[:24]}" if self.peak_text else "")


def _boundary(prev: Moment, cur: Moment) -> str:
    if prev.topic and cur.topic and prev.topic != cur.topic:
        return f"话题变了：{prev.topic} → {cur.topic}"
    gap = cur.ts - prev.ts
    if gap >= GAP_SECONDS:
        return f"静默 {gap/60:.0f} 分钟"
    return ""


def segment(moments: list[Moment], *, start_id: int = 0) -> list[Scene]:
    """按时间排序瞬间，在边界或场景时长上限处切分。"""
    if not moments:
        return []
    ordered = sorted(moments, key=lambda m: m.ts)
    scenes: list[Scene] = []
    cur: list[Moment] = [ordered[0]]

    for m in ordered[1:]:
        prev = cur[-1]
        reason = _boundary(prev, m)
        too_long = (m.ts - cur[0].ts) >= MAX_SCENE_SECONDS
        if reason or too_long:
            scenes.append(_build(cur, start_id + len(scenes)))
            cur = [m]
        else:
            cur.append(m)
    scenes.append(_build(cur, start_id + len(scenes)))
    return scenes


def _build(ms: list[Moment], idx: int) -> Scene:
    s = Scene(
        scene_id=f"s{idx}",
        start=ms[0].ts,
        end=ms[-1].ts,
        mood_at_start=ms[0].mood,
    )
    for m in ms:
        s.moments.append(m)
        if m.speaker:
            s.present[m.speaker] = s.present.get(m.speaker, 0) + 1
        if m.umo:
            s.sessions.add(m.umo)

        if m.speaker and m.salience > 0:
            s.felt_about[m.speaker] = round(
                s.felt_about.get(m.speaker, 0.0) + m.salience, 4)

        if m.salience > s.peak_salience:
            s.peak_salience = m.salience
            s.peak_mood = m.mood
            s.peak_emotions = m.emotions
            s.peak_text = m.text


    s.public = any("GroupMessage" in u for u in s.sessions)
    return s


def is_worth_keeping(sc: Scene) -> bool:
    return sc.peak_salience >= MIN_SCENE_SALIENCE
