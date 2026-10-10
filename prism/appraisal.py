"""根据目标触动、归因、可控性和规范评价生成情绪候选与强度。"""


from __future__ import annotations

from dataclasses import dataclass, field

from .affect import Emotion


RELEVANCE_GATE = 0.15


AGENCY_THRESHOLD = 0.4


COPING_THRESHOLD = 0.45


@dataclass(frozen=True)
class Concerns:
    weights: dict[str, float] = field(default_factory=dict)

    def w(self, check: str) -> float:
        return self.weights.get(check, 1.0)


@dataclass(frozen=True)
class Appraisal:
    novelty: float = 0.0
    pleasantness: float = 0.0
    goal_relevance: float = 0.0


    causal_agency: str = "circumstance"
    outcome_probability: float = 1.0
    expectation_discrepancy: float = 0.0
    goal_conduciveness: float = 0.0
    urgency: float = 0.0


    controllability: float = 0.5
    power: float = 0.5
    adjustment: float = 0.5


    internal_standards: float = 0.0
    external_standards: float = 0.0
    being_watched: bool = False
    fairness_touch: bool = False

    def intrinsic_unpleasantness_strong(self) -> bool:
        return self.pleasantness <= -0.65

    def surprise_gate(self) -> bool:
        return self.novelty >= 0.6 and abs(self.goal_conduciveness) < 0.3


def relevance(a: Appraisal) -> float:
    return max(a.novelty, a.goal_relevance, abs(a.pleasantness))


def implication(a: Appraisal) -> float:
    return min(1.0, max(abs(a.goal_conduciveness), a.urgency,
                        a.expectation_discrepancy * 0.8,
                        abs(a.pleasantness) * 0.8))


def coping_pressure(a: Appraisal) -> float:
    return min(1.0, ((1.0 - a.controllability) + (1.0 - a.power)
                     + (1.0 - a.adjustment)) / 3.0 + 0.35)


def normative_weight(a: Appraisal) -> float:
    return min(1.0, (abs(a.internal_standards) + abs(a.external_standards)) / 2.0 + 0.3)


AMP_EXP_RELEVANCE = 0.80
AMP_EXP_IMPLICATION = 1.10


def amplitude(a: Appraisal, personality=None) -> float:
    rel, imp = relevance(a), implication(a)
    if rel <= 0 or imp <= 0:
        return 0.0
    base = (rel ** AMP_EXP_RELEVANCE) * (imp ** AMP_EXP_IMPLICATION)

    if personality is not None:
        base *= personality.reactivity
    return max(0.0, min(1.0, base))


def _agency_is_other(a: Appraisal) -> bool:
    return a.causal_agency == "other"


def _agency_is_self(a: Appraisal) -> bool:
    return a.causal_agency == "self"


_TOUCHES_OF: dict[int, tuple] = {}


def register_touches(a: Appraisal, touches) -> None:
    _TOUCHES_OF[id(a)] = tuple(touches)
    if len(_TOUCHES_OF) > 512:
        for k in list(_TOUCHES_OF)[:256]:
            _TOUCHES_OF.pop(k, None)


def modal_emotion(a: Appraisal, personality=None, *, tone: str = "") -> Emotion | None:
    intensity = amplitude(a, personality)


    if relevance(a) < RELEVANCE_GATE:
        return None

    def _gated(kind: str, v: float) -> float | None:
        if not tone:
            return v
        from .affect import tone_gate
        g = tone_gate(tone, kind)
        return None if g <= 0 else v * g


    if a.novelty >= 0.7 and abs(a.goal_conduciveness) < 0.3:
        return Emotion("惊讶", max(0.4, intensity))

    good = a.goal_conduciveness > 0

    if good:


        if a.outcome_probability < 0.85:
            return Emotion("期待", intensity)
        if _agency_is_self(a):
            return Emotion("自豪", intensity)
        if _agency_is_other(a):


            personal = any(t.goal in ("belonging", "safety") and t.direction > 0.2
                           for t in _TOUCHES_OF.get(id(a), ()))
            return Emotion("感激" if personal else "喜欢", intensity)
        return Emotion("欣慰", intensity)


    if _agency_is_self(a) and a.internal_standards < -0.2:
        return Emotion("悲伤", intensity)


    if a.external_standards < -0.4:
        return Emotion("厌恶", intensity)


    can_act = a.controllability >= COPING_THRESHOLD and a.power >= COPING_THRESHOLD
    if _agency_is_other(a) or can_act:
        if can_act:
            return Emotion("愤怒", intensity)

    if a.controllability < COPING_THRESHOLD and a.power < COPING_THRESHOLD:
        return Emotion("恐惧", intensity)


    return Emotion("悲伤", intensity)


def _candidates(a: Appraisal) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    rel, imp = relevance(a), implication(a)
    cop = coping_pressure(a)
    amp = amplitude(a)


    if a.novelty >= 0.6 and abs(a.goal_conduciveness) < 0.3:
        out.append(("惊讶", min(0.6, max(0.35, a.novelty * 0.7)), "relevance"))

    good = a.goal_conduciveness > 0
    strong = imp >= 0.3
    can_act = a.controllability >= COPING_THRESHOLD and a.power >= COPING_THRESHOLD
    helpless = a.controllability < COPING_THRESHOLD and a.power < COPING_THRESHOLD


    blameworthy = _agency_is_other(a) and (a.fairness_touch or a.external_standards < -0.2)

    if good:


        if a.outcome_probability < 0.85:
            out.append(("期待", amp, "implication"))
        elif strong:
            if _agency_is_self(a):
                out.append(("自豪", amp, "implication"))
            elif _agency_is_other(a):
                personal = any(t.goal in ("belonging", "safety") and t.direction > 0.2
                               for t in _TOUCHES_OF.get(id(a), ()))
                out.append(("感激" if personal else "喜欢", amp, "implication"))
            else:
                out.append(("欣慰", amp, "implication"))

        if a.novelty < 0.35 and imp < 0.7:
            out.append(("信任", min(0.8, rel * 0.9), "relevance"))
        if a.novelty < 0.3 and imp < 0.4:
            out.append(("平静", 0.3, "relevance"))
    elif strong:


        if a.expectation_discrepancy >= 0.6:
            out.append(("失望", imp * 0.8, "implication"))
        if a.intrinsic_unpleasantness_strong():
            out.append(("厌恶", abs(a.pleasantness), "implication"))


        if helpless and a.goal_relevance >= 0.4:
            out.append(("恐惧", amp * (1.0 + 0.35 * cop), "coping"))
        if can_act and blameworthy:
            out.append(("愤怒", amp, "coping"))


        if a.internal_standards < -0.2:
            out.append(("内疚", amp * min(1.0, abs(a.internal_standards) + 0.4), "normative"))
        if a.causal_agency == "self" and a.internal_standards < -0.2:
            out.append(("悲伤", amp * 0.75, "implication"))
        if a.external_standards < -0.4 or a.being_watched:
            out.append(("羞耻", amp * min(1.0, abs(a.external_standards) + 0.5), "normative"))


        if not out:
            out.append(("悲伤", max(0.25, amp * 0.8), "implication"))

    return out


def appraise_full(a: Appraisal, personality=None, *, tone: str = "") -> list[Emotion]:
    cands = _candidates(a)
    if not cands:
        return []

    scale = 1.0
    if personality is not None:
        scale = max(0.5, min(1.6, personality.reactivity))
    cands = [(k, min(1.0, v * scale), layer) for k, v, layer in cands]


    if tone:
        from .affect import tone_gate
        gated = []
        for k, v, layer in cands:
            g = tone_gate(tone, k)
            if g <= 0:
                continue
            gated.append((k, min(1.0, v * g), layer))
        cands = gated
        if not cands:
            return []


    best: dict[str, tuple[str, float, str]] = {}
    for k, v, layer in cands:
        if layer == "relevance":
            continue
        cur = best.get(layer)
        if cur is None or v > cur[1]:
            best[layer] = (k, v, layer)

    out: list[Emotion] = []

    for k, v, layer in cands:
        if layer == "relevance" and k == "惊讶":
            out.append(Emotion(k, v))
    for layer in ("implication", "coping", "normative"):
        if layer in best:
            k, v, _ = best[layer]
            out.append(Emotion(k, v))

    for k, v, layer in cands:
        if layer == "relevance" and k != "惊讶" and len(out) < 3:
            if not any(o.kind == k for o in out):
                out.append(Emotion(k, v))
    return out[:3]
