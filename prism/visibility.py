"""区分私聊与群聊回忆，将是否适合公开说出的状态附到召回内容。"""


from __future__ import annotations

from dataclasses import dataclass, field


VIS_PRIVATE = 0
VIS_GROUP = 1

_LEVEL_NAME = {VIS_PRIVATE: "私聊", VIS_GROUP: "群聊"}


def visibility_of_scene(scene) -> int:
    if getattr(scene, "public", False):
        return VIS_GROUP
    sessions = getattr(scene, "sessions", None) or set()
    if any("GroupMessage" in s for s in sessions):
        return VIS_GROUP
    return VIS_PRIVATE


@dataclass
class Viewpoint:
    is_group: bool = False
    group_name: str = ""
    peer_name: str = ""

    def level(self) -> int:
        return VIS_GROUP if self.is_group else VIS_PRIVATE

    def describe(self) -> str:
        return f"群「{self.group_name}」" if self.is_group else "私聊"


@dataclass
class Recall:
    key: str
    text: str
    visibility: int
    speakable: bool
    reason: str = ""

    def render(self) -> str:
        if self.speakable:
            return f"· {self.text}"
        return (f"· {self.text}\n"
                f"  （⚠️ 这段是{self.reason}，**你自己心里清楚，"
                f"但此刻不宜当众提起**——不要复述、不要追问、不要点破）")


def can_speak(memory_visibility: int, viewpoint: Viewpoint) -> tuple[bool, str]:
    """私聊回忆在群聊中标为不宜公开，其余范围允许说出。"""
    if memory_visibility == VIS_PRIVATE and viewpoint.is_group:
        return False, "在私聊里说的"
    return True, ""


def filter_for_viewpoint(memories: list, viewpoint: Viewpoint) -> list[Recall]:
    out = []
    for m in memories:
        key = getattr(m, "key", "")
        text = getattr(m, "text", "")
        vis = getattr(m, "visibility", VIS_GROUP)
        ok, why = can_speak(vis, viewpoint)
        out.append(Recall(key=key, text=text, visibility=vis,
                          speakable=ok, reason=why))
    return out


def render_recalls(recalls: list[Recall], viewpoint: Viewpoint) -> str:
    if not recalls:
        return ""
    speakable = [r for r in recalls if r.speakable]
    withheld = [r for r in recalls if not r.speakable]

    parts: list[str] = []

    from .recall import render_memories
    head = render_memories([r.text for r in speakable])
    if head:
        parts.append(head)
    for r in withheld:
        why = (r.reason or "私下里的事").replace("在私聊里说的", "私下里说的")
        parts.append(f"我还想起{r.text}——那是{why}，这会儿不好说出口，心里知道就行")
    if withheld:
        parts.append(
            "这些我不是想不起来，是此刻不适合讲。"
            "我怎么回应仍由眼前的情况和自己的决定而定。"
        )
    if not parts:
        return ""
    return "".join(p if p.endswith("。") else p + "。" for p in parts)


def group_mention_safety(memory, viewpoint: Viewpoint, speaker_is_subject: bool) -> str:
    vis = getattr(memory, "visibility", VIS_GROUP)
    if vis == VIS_GROUP or not viewpoint.is_group:
        return ""
    who = "他自己" if speaker_is_subject else "这个人"
    return (f"（关于{who}，有些事是私下里知道的；我记得这些事，"
            f"也记得眼前是公开场合。）")
