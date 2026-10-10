"""从当前心情、人物与近期经历启动联想召回，筛选记忆强度与可说范围。
默认加固被想起的痕迹；只读场景可显式关闭加固。"""


from __future__ import annotations

from dataclasses import dataclass, field

from .affect import Mood
from .assoc import AssocGraph, rank, seeds_from_now, spread
from .decay import FORGOTTEN, MemoryStore
from .visibility import Viewpoint, can_speak, visibility_of_scene


@dataclass
class Her:
    mood: Mood = field(default_factory=Mood)

    with_person: str = ""

    viewpoint: Viewpoint = field(default_factory=Viewpoint)

    recent_scenes: list[str] = field(default_factory=list)


@dataclass
class Recalled:
    key: str
    text: str
    score: float
    speakable: bool
    why_not: str = ""
    scene: object = None

    def render(self) -> str:
        if self.speakable:
            return render_memories([self.text])
        why = (self.why_not or "私下里的事").replace("在私聊里说的", "私下里说的")
        return (f"我还想起{self.text}——那是{why}，"
                f"这会儿不好说出口，心里知道就行")


_LEADS = ("还有，", "对了，", "我想起，", "另外，", "嗯，还有——")


def render_memories(items: list[str]) -> str:
    if not items:
        return ""
    out = []
    for i, t in enumerate(items):
        t = (t or "").strip()
        if not t:
            continue
        if i == 0:
            out.append("我想起来，" + t)
        else:
            out.append(_LEADS[(i - 1) % len(_LEADS)] + t)
    return "".join(p + ("" if p.endswith("。") else "。") for p in out)


@dataclass
class RecallResult:
    recalled: list[Recalled] = field(default_factory=list)

    unreachable: int = 0
    seeds: dict[str, float] = field(default_factory=dict)

    @property
    def speakable(self) -> list[Recalled]:
        return [r for r in self.recalled if r.speakable]

    @property
    def withheld(self) -> list[Recalled]:
        return [r for r in self.recalled if not r.speakable]

    def render(self) -> str:
        if not self.recalled:
            return ""
        parts: list[str] = []


        head = render_memories([r.text for r in self.speakable])
        if head:
            parts.append(head)

        if self.withheld:


            for r in self.withheld:
                why = (r.why_not or "私下里的事").replace("在私聊里说的", "私下里说的")
                parts.append(
                    f"我还想起{r.text}——不过那是{why}，"
                    f"这话此刻不好说出口，心里知道就行"
                )
            parts.append(
                "这些我不是想不起来，是这会儿不适合讲。"
                "我怎么回应仍由眼前的情况和自己的决定而定。"
            )
        return "".join(p + "。" if not p.endswith("。") else p for p in parts)

    def explain(self) -> str:
        return (f"想起 {len(self.recalled)} 件"
                f"（能说 {len(self.speakable)} / 不说 {len(self.withheld)}），"
                f"另有 {self.unreachable} 件淡到够不着")


def remember(her: Her, graph: AssocGraph, store: MemoryStore, *,
             top_k: int = 5, now: float | None = None,
             reinforce: bool = True) -> RecallResult:
    """联想候选经过强度与内容筛选后附带可说范围，按开关加固所选记忆。"""
    seeds = seeds_from_now(her.mood, person=her.with_person,
                           recent_scenes=her.recent_scenes)
    if not seeds:
        return RecallResult()


    act = spread(graph, seeds, hops=2)


    picked = rank(graph, act, her.mood, top_k=top_k * 3)


    out: list[Recalled] = []
    unreachable = 0
    recall_keys: list[str] = []

    for key, score, narr in picked:
        trace = store.traces.get(key)
        scene = store.scenes.get(key)


        if trace is not None and trace.forgotten(now):
            unreachable += 1
            continue


        text = (getattr(scene, "narrative", "") or "").strip()
        if not text:
            text = (getattr(scene, "meaning", "") or
                    getattr(scene, "peak_text", "") or "").strip()
        if not text:
            continue

        vis = visibility_of_scene(scene) if scene is not None else 1
        ok, why = can_speak(vis, her.viewpoint)
        out.append(Recalled(key=key, text=text[:120], score=score,
                            speakable=ok, why_not=why, scene=scene))
        recall_keys.append(key)
        if len(out) >= top_k:
            break


    if reinforce and recall_keys:
        store.recall(recall_keys, now)

    return RecallResult(recalled=out, unreachable=unreachable, seeds=seeds)
