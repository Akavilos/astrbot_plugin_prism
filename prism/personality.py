"""将五项人格倾向映射为目标偏好、情绪基线和反应参数。
在意事项使用单独倍率，配置变化后由入口重新创建人格对象。"""


from __future__ import annotations

from dataclasses import dataclass, field

from .affect import Mood, personality_to_mood
from .goals import GoalProfile


REACTIVITY_BASE = 0.70
REACTIVITY_SPAN = 0.60


RECOVERY_BASE = 1.00
RECOVERY_FROM_N = -0.45
RECOVERY_FROM_E = +0.30


EXPRESS_BASE = 0.25
EXPRESS_SPAN = 0.65


@dataclass
class Personality:
    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.5


    cares: dict[str, float] = field(default_factory=dict)


    _goals: GoalProfile | None = field(default=None, repr=False)


    @property
    def goals(self) -> GoalProfile:
        if self._goals is None:
            self._goals = GoalProfile.from_personality({
                "openness": self.openness,
                "conscientiousness": self.conscientiousness,
                "extraversion": self.extraversion,
                "agreeableness": self.agreeableness,
                "neuroticism": self.neuroticism,
            })
        return self._goals

    @property
    def reactivity(self) -> float:
        return REACTIVITY_BASE + REACTIVITY_SPAN * self.neuroticism

    @property
    def recovery(self) -> float:
        v = RECOVERY_BASE + RECOVERY_FROM_N * self.neuroticism + RECOVERY_FROM_E * self.extraversion
        return max(0.3, min(2.0, v))

    @property
    def expressiveness(self) -> float:
        return EXPRESS_BASE + EXPRESS_SPAN * self.extraversion

    @property
    def baseline(self) -> Mood:
        return personality_to_mood({
            "openness": self.openness,
            "conscientiousness": self.conscientiousness,
            "extraversion": self.extraversion,
            "agreeableness": self.agreeableness,
            "neuroticism": self.neuroticism,
        })


    def sensitivity(self, key: str) -> float:
        return self.cares.get(key, 1.0)

    def care_about(self, key: str, multiplier: float) -> None:
        self.cares[key] = multiplier


    def profile_line(self) -> str:
        b = self.baseline
        return (f"反应性{self.reactivity:.2f} 恢复{self.recovery:.2f} "
                f"表达{self.expressiveness:.2f} 基线心情({b.p:+.2f},{b.a:+.2f},{b.d:+.2f})")

    def top_goals(self, n: int = 4) -> list[tuple[str, float]]:
        return self.goals.top(n)


def example_innocent() -> Personality:
    return Personality(openness=0.6, conscientiousness=0.6, extraversion=0.4,
                       agreeableness=0.9, neuroticism=0.75)


def example_playboy() -> Personality:
    return Personality(openness=0.7, conscientiousness=0.35, extraversion=0.9,
                       agreeableness=0.15, neuroticism=0.25)
