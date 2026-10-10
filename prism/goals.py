"""定义目标权重、关系参数和目标触动事件，支持从人格生成目标偏好。"""


from __future__ import annotations

from dataclasses import dataclass, field


GOALS: dict[str, str] = {
    "belonging":   "被爱、被在乎（满足→喜悦；违背→悲伤/失落）",
    "esteem":      "自尊、被认可（满足→喜悦/自豪；违背→羞耻/愤怒）",
    "autonomy":    "自主、不被支配（满足→平静；违背→愤怒/恐惧）",
    "safety":      "安全、免于威胁（满足→平静/信任；违背→恐惧）",
    "novelty":     "新鲜、好奇（满足→期待/惊讶；违背→无聊/平静）",
    "fairness":    "公平、被公正对待（满足→信任；违背→愤怒）",
    "no_harm":     "不伤害他人、守诺（满足→信任；违背→内疚）",
    "competence":  "胜任、把事情做好（满足→喜悦；违背→失望）",
    "aesthetic":   "美、趣味（满足→喜悦/平静）",
}


DEFAULT_WEIGHTS: dict[str, float] = {
    "belonging": 0.55, "esteem": 0.55, "autonomy": 0.50, "safety": 0.60,
    "novelty": 0.40, "fairness": 0.45, "no_harm": 0.50, "competence": 0.45,
    "aesthetic": 0.35,
}


PERSONALITY_PROFILE: dict[str, dict[str, float]] = {

    "agreeableness":      {"belonging": +0.40, "no_harm": +0.35, "fairness": +0.20},

    "neuroticism":        {"safety": +0.35, "belonging": +0.25, "esteem": +0.15},

    "extraversion":       {"novelty": +0.35, "belonging": +0.15},

    "openness":           {"novelty": +0.20, "aesthetic": +0.30},

    "conscientiousness":  {"competence": +0.35, "no_harm": +0.15},
}


@dataclass
class GoalProfile:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def weight(self, goal: str) -> float:
        return self.weights.get(goal, 0.0)

    def top(self, n: int = 4) -> list[tuple[str, float]]:
        return sorted(self.weights.items(), key=lambda kv: -kv[1])[:n]

    @classmethod
    def from_personality(cls, big5: dict[str, float]) -> "GoalProfile":
        w = dict(DEFAULT_WEIGHTS)
        for trait, effects in PERSONALITY_PROFILE.items():
            v = big5.get(trait)
            if v is None:
                continue
            deviation = (float(v) - 0.5) * 2.0
            for goal, coef in effects.items():
                w[goal] = w.get(goal, 0.0) + coef * deviation

        return cls({k: max(0.05, min(1.0, v)) for k, v in w.items()})


@dataclass
class Relation:
    closeness: float = 0.3
    attraction: float = 0.0
    history: float = 0.3
    trust: float = 0.5

    def weight_of(self, goal: str) -> float:
        relational = goal in ("belonging", "no_harm", "fairness")
        if relational:
            return 0.35 + 0.65 * self.closeness

        return 0.40 + 0.60 * (1.0 - self.closeness)


def relation_for(profile: GoalProfile, closeness: float, attraction: float,
                 history: float = 0.3, trust: float = 0.5) -> Relation:
    return Relation(closeness=closeness, attraction=attraction,
                    history=history, trust=trust)


@dataclass(frozen=True)
class Touch:
    goal: str
    direction: float
    note: str = ""


@dataclass(frozen=True)
class GoalEvent:
    name: str
    touches: tuple[Touch, ...]
    base_novelty: float = 0.5
    agency: str = "other"


EVENTS: dict[str, GoalEvent] = {
    "confession": GoalEvent(
        name="被表白",
        touches=(Touch("belonging", 0.0, "方向待定，由关系决定"),),
        base_novelty=0.8, agency="other",
    ),
    "betrayal": GoalEvent(
        name="被辜负",
        touches=(Touch("fairness", -0.9), Touch("safety", -0.5),
                 Touch("belonging", -0.6)),
        base_novelty=0.6, agency="other",
    ),
    "praise": GoalEvent(
        name="被夸",
        touches=(Touch("esteem", 0.5),),
        base_novelty=0.3, agency="other",
    ),
    "threat": GoalEvent(
        name="受到威胁",
        touches=(Touch("safety", -0.9), Touch("autonomy", -0.4)),
        base_novelty=0.7, agency="circumstance",
    ),
    "smalltalk": GoalEvent(
        name="寻常闲聊",
        touches=(Touch("belonging", 0.1),),
        base_novelty=0.05, agency="other",
    ),
    "promise_broken": GoalEvent(
        name="承诺被打破",
        touches=(Touch("fairness", -0.7), Touch("belonging", -0.5)),
        base_novelty=0.5, agency="other",
    ),
    "promise_made": GoalEvent(
        name="得到承诺",
        touches=(Touch("belonging", 0.5), Touch("safety", 0.2)),
        base_novelty=0.4, agency="other",
    ),


    "correction": GoalEvent(
        name="被纠正",
        touches=(Touch("esteem", -0.5), Touch("fairness", -0.2)),
        base_novelty=0.35, agency="other",
    ),


    "friction": GoalEvent(
        name="关系摩擦",
        touches=(Touch("esteem", -0.35), Touch("autonomy", -0.30),
                 Touch("belonging", -0.15)),
        base_novelty=0.3, agency="other",
    ),
}
