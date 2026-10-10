"""按会话维护进行中的场景，积累瞬间并封存为经历对象。"""


from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class OpenScene:
    umo: str
    scene_id: str
    moments: list = field(default_factory=list)
    topic: str = ""
    started_at: float = 0.0


    last_topic_check: float = 0.0

    def append(self, moment) -> None:
        self.moments.append(moment)

    def age(self, now: float | None = None) -> float:
        return (now or time.time()) - self.started_at

    def seal(self):
        from .scene import segment
        sc = segment(self.moments)[0] if self.moments else None
        if sc is not None:
            sc.scene_id = self.scene_id
        return sc


class SceneBook:
    def __init__(self) -> None:
        self.open: dict[str, OpenScene] = {}
        self._seq = 0

    def new_id(self) -> str:
        self._seq += 1
        return f"s{int(time.time())}-{self._seq}"

    def get_or_open(self, umo: str, ts: float) -> OpenScene:
        sc = self.open.get(umo)
        if sc is None:
            sc = OpenScene(umo=umo, scene_id=self.new_id(), started_at=ts)
            self.open[umo] = sc
        return sc

    def seal(self, umo: str):
        sc = self.open.pop(umo, None)
        if sc is None or not sc.moments:
            return None
        return sc.seal()
