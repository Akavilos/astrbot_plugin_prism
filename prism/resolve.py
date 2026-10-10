"""把目标触动和关系参数转换为事件评价，供情绪评估模块使用。"""


from __future__ import annotations

from .appraisal import Appraisal
from .goals import GoalEvent, GoalProfile, Relation


RELEVANCE_FLOOR = 0.15


GUILT_COUPLING = 0.9


STRANGENESS_NOVELTY = 0.5


def resolve(ev: GoalEvent, profile: GoalProfile, rel: Relation,
            *, personality=None) -> Appraisal:
    weighted: list[tuple[float, float, str]] = []
    for t in ev.touches:
        w = profile.weight(t.goal) * rel.weight_of(t.goal)
        weighted.append((t.direction, w, t.goal))

    goal_relevance = min(1.0, max((w for _, w, _ in weighted), default=0.0))


    num = sum(d * w for d, w, _ in weighted)
    den = sum(w for _, w, _ in weighted) or 1.0
    goal_conduciveness = max(-1.0, min(1.0, num / den))


    novelty = min(1.0, ev.base_novelty + STRANGENESS_NOVELTY * (1.0 - rel.history))


    pleasantness = goal_conduciveness * 0.85


    unfair = any(t.goal == "fairness" and t.direction < -0.2 for t in ev.touches)

    base = Appraisal(
        novelty=novelty,
        pleasantness=pleasantness,
        goal_relevance=goal_relevance,
        causal_agency=ev.agency,
        outcome_probability=1.0,
        expectation_discrepancy=min(1.0, novelty * 0.8),
        goal_conduciveness=goal_conduciveness,
        urgency=min(1.0, abs(goal_conduciveness) * 0.7),

        controllability=0.25 + 0.55 * rel.closeness,
        power=0.30 + 0.45 * rel.closeness + 0.15 * rel.trust,
        adjustment=0.40 + 0.30 * rel.history,
        fairness_touch=unfair,
    )
    result = _apply_relation_effects(base, ev, profile, rel)


    try:
        from .appraisal import register_touches
        register_touches(result, ev.touches)
    except Exception:
        pass
    return result


def _apply_relation_effects(a: Appraisal, ev: GoalEvent, profile: GoalProfile,
                            rel: Relation) -> Appraisal:
    dirs = {t.goal: t.direction for t in ev.touches}
    if dirs.get("belonging") == 0.0 and "belonging" in dirs:
        attraction = rel.attraction

        belonging_dir = attraction
        if belonging_dir >= 0:
            goal_conduciveness = belonging_dir
            internal = 0.0
        else:
            goal_conduciveness = belonging_dir * 0.5

            internal = -GUILT_COUPLING * abs(attraction) * profile.weight("no_harm")

        return Appraisal(
            novelty=a.novelty,
            pleasantness=goal_conduciveness * 0.85,
            goal_relevance=max(a.goal_relevance, 0.55),
            causal_agency=a.causal_agency,
            outcome_probability=a.outcome_probability,
            expectation_discrepancy=a.expectation_discrepancy,
            goal_conduciveness=goal_conduciveness,
            urgency=a.urgency,
            controllability=a.controllability,
            power=a.power,
            adjustment=a.adjustment,
            internal_standards=internal,
            external_standards=0.0,
            being_watched=False,
        )
    return a


def resolve_named(event: str, profile: GoalProfile, rel: Relation,
                  **kw) -> Appraisal:
    from .goals import EVENTS
    if event not in EVENTS:
        raise KeyError(f"未知事件：{event}（可选：{list(EVENTS)}）")
    return resolve(EVENTS[event], profile, rel, **kw)
