"""维护场景、人物、话题和情绪之间的联想关系。
扩散激活受连接权重、邻居数量、传播轮数与最小增益限制。"""


from __future__ import annotations

import math
import time
from dataclasses import dataclass, field


GAMMA = 0.55


MAX_HOPS = 2


MIN_COOCCUR = 3


FAN_EXP = 0.50


TOPIC_LINK_WEIGHT = 1.4


MIN_TOPIC_SCENES = 3


MAX_ACTIVATION = 1.0


CUTOFF = 0.01


MOOD_CONGRUENCE_WEIGHT = 0.45


@dataclass
class Node:
    key: str
    kind: str
    data: dict = field(default_factory=dict)


class AssocGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}

        self.edges: dict[str, dict[str, float]] = {}


        self._cooccur: dict[tuple[str, str], int] = {}

    def finalize_people_edges(self) -> int:
        n = 0
        for (a, b), cnt in self._cooccur.items():
            if cnt < MIN_COOCCUR:
                continue
            ka, kb = f"person:{a}", f"person:{b}"
            if ka in self.nodes and kb in self.nodes:

                self.link(ka, kb, min(1.0, 0.4 + 0.2 * math.log1p(cnt)))
                n += 1
        return n


    def add_node(self, key: str, kind: str, **data) -> None:
        self.nodes[key] = Node(key=key, kind=kind, data=data)
        self.edges.setdefault(key, {})

    def link(self, a: str, b: str, weight: float = 1.0) -> None:
        if a == b or a not in self.nodes or b not in self.nodes:
            return
        w = min(2.0, self.edges[a].get(b, 0.0) + weight)
        self.edges[a][b] = w
        self.edges[b][a] = w

    def fan(self, key: str) -> int:
        return len(self.edges.get(key, {}))

    def neighbors(self, key: str) -> dict[str, float]:
        return self.edges.get(key, {})


    def add_scene(self, sc) -> None:
        skey = f"scene:{sc.scene_id}"
        self.add_node(skey, "scene", start=sc.start, end=sc.end,
                      peak_salience=sc.peak_salience,
                      narrative=sc.narrative, public=sc.public,
                      peak_mood=(sc.peak_mood.p, sc.peak_mood.a, sc.peak_mood.d))


        for person in sc.present:
            pkey = f"person:{person}"
            if pkey not in self.nodes:
                self.add_node(pkey, "person")
            self.link(skey, pkey, 1.0)


        people = list(sc.present)
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                pair = tuple(sorted((a, b)))
                self._cooccur[pair] = self._cooccur.get(pair, 0) + 1

    def link_topic_scenes(self, scenes: list) -> int:
        by_topic: dict[str, list[str]] = {}
        for sc in scenes:
            t = (getattr(sc, "topic", "") or "").strip()
            if not t:
                continue
            by_topic.setdefault(t, []).append(f"scene:{sc.scene_id}")

        n = 0
        for topic, keys in by_topic.items():
            if len(keys) < MIN_TOPIC_SCENES:
                continue

            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    if a in self.nodes and b in self.nodes:
                        self.link(a, b, TOPIC_LINK_WEIGHT)
                        n += 1
        return n

    def link_adjacent_scenes(self, scenes: list) -> None:
        ordered = sorted(scenes, key=lambda s: s.start)
        for prev, cur in zip(ordered, ordered[1:]):
            a, b = f"scene:{prev.scene_id}", f"scene:{cur.scene_id}"
            if a in self.nodes and b in self.nodes:

                gap = max(1.0, cur.start - prev.end)
                self.link(a, b, 1.0 / (1.0 + math.log1p(gap / 60.0)))


def spread(graph: AssocGraph, seeds: dict[str, float], *,
           hops: int = MAX_HOPS, gamma: float = GAMMA) -> dict[str, float]:
    """从联想起点逐层传播激活，邻居越多单边增益越小。"""
    activation: dict[str, float] = dict(seeds)
    frontier = dict(seeds)

    for _ in range(max(0, hops)):
        nxt: dict[str, float] = {}
        for key, amount in frontier.items():
            nb = graph.neighbors(key)
            if not nb:
                continue


            fan = max(1, len(nb))
            inhibition = fan ** (-FAN_EXP)
            for target, w in nb.items():


                gain = amount * gamma * inhibition * min(1.0, w)
                if gain < CUTOFF:
                    continue
                nxt[target] = nxt.get(target, 0.0) + gain
        if not nxt:
            break
        for k, v in nxt.items():
            activation[k] = min(MAX_ACTIVATION, activation.get(k, 0.0) + v)
        frontier = nxt

    return dict(sorted(activation.items(), key=lambda kv: -kv[1]))


def seeds_from_now(mood, *, person: str = "", recent_scenes: list[str] | None = None,
                   recent_scene_window: int = 2) -> dict[str, float]:
    s: dict[str, float] = {}
    if person:
        s[f"person:{person}"] = 1.0
    for key in (recent_scenes or [])[-max(1, recent_scene_window):]:
        s[key] = s.get(key, 0.0) + 0.8
    return s


def mood_congruence(mood, scene_mood_tuple) -> float:
    if not scene_mood_tuple:
        return 0.5
    sp, sa, sd = scene_mood_tuple

    dot = mood.p * sp + mood.a * sa
    np_ = math.sqrt(mood.p ** 2 + mood.a ** 2) or 1e-6
    ns = math.sqrt(sp ** 2 + sa ** 2) or 1e-6
    cos = dot / (np_ * ns)
    mag = min(1.0, (np_ + ns) / 2.0)
    return max(0.0, (cos + 1) / 2) * (0.5 + 0.5 * mag)


def rank(graph: AssocGraph, activation: dict[str, float], mood, *,
         top_k: int = 5) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for key, act in activation.items():
        node = graph.nodes.get(key)
        if not node or node.kind != "scene":
            continue

        cong = mood_congruence(mood, node.data.get("peak_mood"))
        score = act * (1.0 - MOOD_CONGRUENCE_WEIGHT + MOOD_CONGRUENCE_WEIGHT * cong)

        score *= (0.5 + 0.5 * min(1.0, node.data.get("peak_salience", 0.0) * 4))
        out.append((key, score, node.data.get("narrative", "")))
    out.sort(key=lambda x: -x[1])
    return out[:top_k]
