"""保存理解与纠正记录，根据纠正线索选择最近尚未被纠正的理解。"""


from __future__ import annotations

from dataclasses import dataclass, field, replace


CORRECTION_CUES: tuple[str, ...] = (
    "不是我要", "不是那个意思", "不是这意思", "我问的是", "我说的是",
    "我没说", "你理解错", "你误会", "别误会", "不是让你",
    "不是跟你", "搞错了吧", "不是这个", "我没那个意思",
)


def is_correction(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(cue in t for cue in CORRECTION_CUES)


@dataclass
class Understanding:
    utter: str
    read_as: str
    confidence: float = 0.5

    corrected_by: str = ""
    was_wrong: bool = False

    def is_open(self) -> bool:
        return not self.corrected_by


@dataclass
class Correction:
    target: Understanding
    by: str
    acknowledged: bool
    note: str = ""

    def explain(self) -> str:
        state = "反应过来" if self.acknowledged else "还没反应过来"
        return (f"「{self.target.utter[:20]}」当时理解成 [{self.target.read_as}]，"
                f"被「{self.by[:20]}」指出 → {state}")


def correct(target: Understanding, by: str, *, receptive: bool = True) -> Correction:
    if not receptive:


        return Correction(target=target, by=by, acknowledged=False,
                          note="被指出，但她没接受（嘴硬/否认）")

    target.was_wrong = True
    target.corrected_by = by
    return Correction(target=target, by=by, acknowledged=True,
                      note="她反应过来自己理解错了")


def find_target(candidates: list[Understanding], by: str) -> Understanding | None:
    for u in reversed(candidates or []):
        if u.is_open():
            return u
    return None
