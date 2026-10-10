"""按人物累计情绪互动并持久化主观感受，生成可读关系底色。"""


from __future__ import annotations

from .runtime import elapsed_since

import time

from astrbot.api import logger

from .affect import MIN_INTENSITY, Mood, update as affect_update


MIN_MOMENTS = 4


STALE_AFTER = 7 * 24 * 3600


class InterpersonalAffect:
    def __init__(self, store) -> None:
        self.store = store

        self._state: dict[str, dict] = {}


    def get(self, person_id: str) -> Mood | None:
        st = self._state.get(person_id)
        if not st:
            return None
        m = st.get("mood") or [0.0, 0.0, 0.0]
        return Mood(float(m[0]), float(m[1]), float(m[2]))

    def moments(self, person_id: str) -> int:
        return int((self._state.get(person_id) or {}).get("n") or 0)


    def absorb(self, person_id: str, emotions: list) -> None:
        if not person_id or not emotions:
            return
        active = [e for e in emotions
                  if getattr(e, "intensity", 0.0) > MIN_INTENSITY and e.pad()]
        if not active:
            return

        st = self._state.setdefault(
            person_id, {"mood": [0.0, 0.0, 0.0], "updated": 0.0, "n": 0,
                        "last": "", "kinds": {}})

        cur = Mood(*[float(x) for x in st["mood"]])


        gap = elapsed_since(float(st.get("updated") or 0))
        if gap > STALE_AFTER:
            cur = Mood(cur.p * 0.5, cur.a * 0.5, cur.d * 0.5)

        push = affect_update(cur, active)
        st["mood"] = [push.after.p, push.after.a, push.after.d]
        st["updated"] = time.time()
        st["n"] = int(st.get("n") or 0) + 1


        kinds = st.setdefault("kinds", {})
        for e in active:
            k = getattr(e, "kind", "")
            if k:
                kinds[k] = int(kinds.get(k) or 0) + 1
        st["last"] = "、".join(
            sorted(kinds, key=lambda k: (-int(kinds[k]), k))[:2])[:40]


    def describe(self, person_id: str, name: str = "", now: float | None = None) -> str:
        st = self._state.get(person_id)
        if not st or int(st.get("n") or 0) < MIN_MOMENTS:
            return ""
        m = Mood(*[float(x) for x in st["mood"]])
        if m.magnitude() < 0.08:


            return f"（我对{name or '这个人'}说不上什么固定的感觉，忽冷忽热，没拧成一股。）"


        who = (name or "").strip() or "他"
        line = f"心里{_word_p(m.p)}"
        if m.a >= 0.15:
            line += "，一提起就有点起伏"
        elif m.a <= -0.15:
            line += "，提起来也没什么劲"
        if m.d <= -0.15:
            line += "，在他面前说不上有多少底气"
        elif m.d >= 0.15:
            line += "，在他面前我是站得住的"

        last = (st.get("last") or "").strip()
        tail = f"——跟{who}，多是{last}。" if last else f"——这是跟{who}处出来的。"

        if now and elapsed_since(float(st.get("updated") or 0), now) > STALE_AFTER:
            tail += "（不过有些日子没见了，未必还作数）"
        return line + tail


    def save(self, person_id: str) -> None:
        st = self._state.get(person_id)
        if not st:
            return
        try:
            self.store.save_person(person_id, st, now=st.get("updated"))
        except Exception:
            logger.debug("[prism·対人] 落盘失败 %s", person_id, exc_info=True)

    def load_all(self) -> int:
        n = 0
        try:
            for pid in self.store.list_person_ids():
                d = self.store.load_person(pid)
                if isinstance(d, dict) and "mood" in d:
                    self._state[pid] = d
                    n += 1
        except Exception:
            logger.debug("[prism·対人] 恢复失败", exc_info=True)
        if n:
            logger.info("[prism·対人] 恢复了 %d 个人的情感底色", n)
        return n


def _word_p(v: float) -> str:
    if v >= 0.5:
        return "是暖的"
    if v >= 0.15:
        return "还算暖"
    if v > -0.15:
        return "是平的"
    if v > -0.5:
        return "有点堵"
    return "很不好受"
