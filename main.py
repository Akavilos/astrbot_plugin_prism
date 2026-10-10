"""棱镜框架入口：理解互动，更新主观感受，封存经历并提供读取接口。
角色回复由接入方生成；共同运行时通过运行控制接口协调暂停。"""


from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import astrbot.api.star as star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

from .prism import runtime as runtime_control
from .prism import settings as settings_config
from .prism.affect import Mood
from .prism.appraisal import appraise_full
from .prism.assoc import AssocGraph
from .prism.batch import read_batch
from .prism.boundary import judge_async, should_split, hard_boundary
from .prism.decay import MemoryStore
from .prism.open_scene import SceneBook
from .prism.page_api import PageApi
from .prism.personality import Personality
from .prism.reading import RelationRead
from .prism.interperson import InterpersonalAffect
from .prism.reading_model import read_async
from .prism.provider_pick import pick as pick_provider
from .prism.recall import Her, remember
from .prism.resolve import resolve
from .prism.scene import Moment, segment
from .prism.scene_writer import write_scene
from .prism.state import snapshot
from .prism.store import PrismStore
from .prism.visibility import Viewpoint

PLUGIN_NAME = "astrbot_plugin_prism"


PRIORITY = 100


RECALL_EVERY = 12


MAX_OPEN_SECONDS = 2 * 3600


TOPIC_CHECK_INTERVAL = 3 * 60


@star.register(PLUGIN_NAME, "Akavilos", "棱镜 · 人类记忆 (Prism)", "0.2.3")
class PrismPlugin(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        settings_config.migrate(self.config)
        self.data_dir = star.StarTools.get_data_dir(PLUGIN_NAME)


        self.store = PrismStore(self.data_dir / "prism.db")


        self.graph = AssocGraph()
        self.memory = MemoryStore()


        self.personality = self._load_personality()
        self.mood: Mood = self.personality.baseline
        self.mood_state = snapshot(self.mood, personality=self.personality)


        self.relations: dict[str, RelationRead] = {}


        self.interperson = InterpersonalAffect(self.store)


        self._last_appraisal = None
        self._turn_appraisals = {}
        self._turn_emotions = {}
        self._turn_relations = {}


        self._llm_turns: dict[str, float] = {}


        self.scene_book = SceneBook()

        self._current_person: dict[str, tuple[str, float]] = {}

        self.page_api = PageApi(self)
        self._recall_task: asyncio.Task | None = None


    async def initialize(self) -> None:
        """恢复经历、联想图与主观状态，注册面板并启动场景维护循环。"""
        self._apply_half_life()
        logger.info("[prism] 已加载。enabled=%s（false 时完全不介入）",
                    self._cfg("enabled", True))


        try:
            cnt = self.store.load_into(self.graph, self.memory)
            logger.info("[prism] 记忆已恢复：%s", cnt)
        except Exception:
            logger.exception("[prism] 恢复记忆失败，从空的开始")
        try:
            self._restore_mood()
        except Exception:
            logger.exception("[prism] 恢复心情失败，用默认值")
        try:
            self.interperson.load_all()
        except Exception:
            logger.exception("[prism] 恢复対人感情失败，用默认值")


        if hasattr(self.context, "register_web_api"):
            try:
                self.page_api.register_routes()
                logger.info("[prism] 面板路由注册成功")
            except Exception:
                logger.exception("[prism] 注册面板失败")
        else:
            logger.warning("[prism] 框架不支持 register_web_api，面板不可用")

        self._recall_task = asyncio.create_task(self._recall_loop())

    async def terminate(self) -> None:
        if self._recall_task:
            self._recall_task.cancel()
            try:
                await self._recall_task
            except (asyncio.CancelledError, Exception):
                pass
        try:

            await self._flush_all()
            self.store.persist_from(self.graph, self.memory)
            self.store.close()
        except Exception:
            logger.exception("[prism] 退出时保存失败")
        logger.info("[prism] 已卸载。")


    def _cfg(self, key: str, default):
        return settings_config.value(self.config, key, default)

    def _load_personality(self) -> Personality:
        def g(k, d):
            try:
                return float(self._cfg(k, d))
            except (TypeError, ValueError):
                return d


        cares_raw = self._cfg("cares", "{}")
        cares: dict = {}
        if isinstance(cares_raw, dict):
            cares = cares_raw
        elif isinstance(cares_raw, str) and cares_raw.strip():
            try:
                parsed = json.loads(cares_raw)
                if isinstance(parsed, dict):
                    cares = parsed
            except Exception:
                logger.warning("[prism] cares 不是合法 JSON，忽略：%r", cares_raw[:60])
        return Personality(
            openness=g("trait_openness", 0.6),
            conscientiousness=g("trait_conscientiousness", 0.5),
            extraversion=g("trait_extraversion", 0.55),
            agreeableness=g("trait_agreeableness", 0.8),
            neuroticism=g("trait_neuroticism", 0.6),
            cares={str(k): float(v) for k, v in cares.items()
                   if isinstance(v, (int, float))},
        )

    def _enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    def _apply_half_life(self) -> None:
        try:
            from .prism import decay
            v = float(self._cfg("half_life_hours", decay.HALF_LIFE_HOURS))
            if v > 0:
                decay.HALF_LIFE_HOURS = v
        except Exception:
            pass


    @filter.on_llm_request(priority=PRIORITY)
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        if not runtime_control.allowed():
            return
        event.set_extra("_prism_runtime_epoch", runtime_control.checkpoint())
        try:
            self._turn_appraisals.pop(event.unified_msg_origin, None)
            self._turn_emotions.pop(event.unified_msg_origin, None)
            self._turn_relations.pop(event.unified_msg_origin, None)
            self._llm_turns[event.unified_msg_origin] = time.time()
            if len(self._llm_turns) > 256:

                for k in list(self._llm_turns)[:128]:
                    self._llm_turns.pop(k, None)
        except Exception:
            pass
        try:
            await self._observe(event)
        except Exception:
            logger.exception("[prism] 观察本轮出错，跳过（不影响对话）")

    async def _observe(self, event: AstrMessageEvent) -> None:
        """理解当前互动并更新心情、人物感受与本轮信号；提交前核对运行代次。"""
        if not self._enabled() or not runtime_control.allowed():
            return
        epoch = runtime_control.checkpoint()

        umo = event.unified_msg_origin
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return

        sender = _sender(event)
        sender_id = _sender_id(event)
        person = f"{_platform(umo)}:{sender_id}" if sender_id else ""
        is_group = "GroupMessage" in umo


        provider = await pick_provider(self.context, umo,
                                           self._cfg("provider_id", ""))
        if provider is None:
            logger.debug("[prism] 没有可用 provider，跳过读心")
            return

        _open = self.scene_book.open.get(umo)
        history = [
            ("[我]" if m.is_hers else "[同一发言人]" if m.speaker == person else "[其他人]") + m.text
            for m in (_open.moments if _open else [])
        ][-8:]
        base = self.relations.get(person) if person else None
        if base is None and person:


            _d = self.interperson._state.get(person) or {}
            if _d:
                base = RelationRead(closeness=0.3, attraction=0.0,
                                    history=min(1.0, _d.get('n', 0) / 20.0),
                                    trust=0.5)
        try:
            read = await read_async(provider, text, history=history, base_relation=base)
        except Exception as error:
            runtime_control.record_failure("reading", error)
            logger.exception("[prism] 读心失败，本条跳过")
            return
        if not runtime_control.allowed(epoch):
            return


        if person:
            self.relations[person] = read.relation


        appraisal = resolve(read.event, self.personality.goals,
                            read.relation.to_relation(), personality=self.personality)


        emotions = appraise_full(appraisal, self.personality,
                                 tone=getattr(read, "tone", ""))
        from .prism.affect import update as mood_update


        push = mood_update(self.mood, emotions)


        self._last_appraisal = appraisal
        self._turn_appraisals[umo] = appraisal
        message_id = str(getattr(event.message_obj, "message_id", "") or self._llm_turns[umo])
        self._turn_relations[umo] = {
            **read.relationship,
            "person_id": person,
            "event_key": hashlib.sha256(f"{umo}|{sender_id}|{message_id}".encode()).hexdigest(),
        } if read.relationship else {}
        self._turn_emotions[umo] = "、".join(f"{e.kind}{e.intensity:.2f}" for e in emotions)
        self.mood = push.after
        self.mood_state = snapshot(
            self.mood, salience=push.salience,
            emotions="、".join(f"{e.kind}{e.intensity:.2f}" for e in emotions),
            personality=self.personality)
        self._persist_mood()


        moment = Moment(
            ts=time.time(), speaker=person or sender, text=text,
            is_hers=False, mood=self.mood,
            emotions=tuple((e.kind, e.intensity) for e in emotions),
            salience=push.salience, umo=umo, event=read.event.name,
            topic=read.topic,
        )
        self._current_person[umo] = (person, time.time())


        if person and getattr(appraisal, "causal_agency", "") == "other":
            self.interperson.absorb(person, emotions)
            self.interperson.save(person)

        if push.salience > 0.05:
            logger.info("[prism] %s → %s，痕迹 %.3f",
                        text[:16], read.event.name, push.salience)


        await self._append_moment(umo, moment, provider, read.topic)


    @filter.on_decorating_result(priority=-1)
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        try:
            if not self._enabled() or not runtime_control.allowed(
                    event.get_extra("_prism_runtime_epoch", None)):
                return
            umo = event.unified_msg_origin


            armed_at = self._llm_turns.pop(umo, None)
            if armed_at is None or (time.time() - armed_at) > 300:
                logger.info("[prism] 这条输出不是她说的（非 LLM 轮），不记：%s",
                            umo[-14:] if umo else "-")
                return

            result = getattr(event, "get_result", lambda: None)()
            text = ""
            if result is not None:
                try:
                    text = (result.get_plain_text() or "").strip()
                except Exception:
                    text = ""


            raw_len = len(text)
            text = _strip_generated_tags(text)


            logger.info("[prism] 她说的：清洗 %d → %d 字  结果=%r",
                        raw_len, len(text), text[:40])
            if not text:
                return

            _sc = self.scene_book.get_or_open(umo, time.time())
            _sc.append(Moment(
                ts=time.time(), speaker=self._current_person.get(umo, ("", 0))[0],
                text=text, is_hers=True, mood=self.mood,
                salience=0.0, umo=umo,
            ))
            snapshot = _sc.seal()
            if snapshot:
                self.store.save_scene(snapshot)
        except Exception:
            logger.exception("[prism] 记录她的话失败，跳过")


    async def _append_moment(self, umo: str, moment, provider, topic: str) -> None:
        if not runtime_control.allowed():
            return
        epoch = runtime_control.checkpoint()
        book = self.scene_book
        sc = book.get_or_open(umo, moment.ts)
        sc.append(moment)


        if await self._should_seal(sc, moment, provider, topic):
            await self._seal(umo, provider, reason="话题变了")

        if not runtime_control.allowed(epoch):
            return


        try:
            snap = sc.seal()
            if snap is not None:
                self.store.save_scene(snap)
        except Exception:
            logger.debug("[prism] 落盘失败（下一轮会再试）", exc_info=True)

    async def _should_seal(self, sc, moment, provider, topic: str) -> bool:
        if not runtime_control.allowed():
            return False
        epoch = runtime_control.checkpoint()
        if not sc.moments:
            return False
        first = sc.moments[0]


        if runtime_control.elapsed_since(first.ts, moment.ts) >= MAX_OPEN_SECONDS:
            return True

        if len(sc.moments) >= max(6, int(self._cfg("recall_every", RECALL_EVERY)) * 2):
            return True


        if not topic or topic == sc.topic:
            if topic:
                sc.topic = topic
            return False
        if runtime_control.elapsed_since(sc.last_topic_check, moment.ts) < TOPIC_CHECK_INTERVAL:
            return False

        sc.last_topic_check = moment.ts
        try:
            same, conf = await judge_async(
                provider, [m.text for m in sc.moments[-6:]], [moment.text])
            if not runtime_control.allowed(epoch):
                return False
            if should_split(same, conf):
                logger.info("[prism] 话题变了（%.2f）：%r → %r", conf, sc.topic, topic)
                return True
        except Exception:
            logger.debug("[prism] 话题判断失败，继续同一场景", exc_info=True)
        if not runtime_control.allowed(epoch):
            return False
        sc.topic = topic
        return False

    async def _seal(self, umo: str, provider, reason: str = "") -> None:
        """整理场景文字后再次确认场景未被并发更新，再封存与保存。"""
        if not runtime_control.allowed():
            return
        epoch = runtime_control.checkpoint()
        open_scene = self.scene_book.open.get(umo)
        if open_scene is None or not open_scene.moments:
            return
        moment_count = len(open_scene.moments)
        sc = open_scene.seal()
        if sc is None:
            return


        if self._cfg("write_narrative", True):
            try:
                sc = await write_scene(provider, sc)
            except Exception as error:
                runtime_control.record_failure("narrative", error)
                logger.exception("[prism] 写叙事失败，留空（场景照常存）")

        if not runtime_control.allowed(epoch):
            return
        if self.scene_book.open.get(umo) is not open_scene or len(open_scene.moments) != moment_count:
            return
        self.scene_book.open.pop(umo)
        self.graph.add_scene(sc)
        self.memory.add(sc, now=sc.end)

        try:
            self.graph.link_adjacent_scenes(list(self.memory.scenes.values()))
            self.graph.link_topic_scenes(list(self.memory.scenes.values()))
            self.graph.finalize_people_edges()
        except Exception:
            logger.exception("[prism] 补边失败")
        try:
            self.store.persist_from(self.graph, self.memory)
        except Exception:
            logger.exception("[prism] 落盘失败")

        logger.info("[prism] 封存场景 %s（%s）：%d 刻，痕迹 %.3f",
                    sc.scene_id, reason or "结束", len(sc.moments), sc.peak_salience)


    async def _recall_loop(self) -> None:
        """按安静时长封存会话场景，暂停阶段不执行整理。"""
        while True:
            try:
                await asyncio.sleep(60)
                if not self._enabled() or not runtime_control.allowed():
                    continue
                now = time.time()
                stale = float(self._cfg("idle_seal_minutes", 10)) * 60
                for umo, sc in list(self.scene_book.open.items()):
                    if not sc.moments:
                        continue
                    if runtime_control.elapsed_since(sc.moments[-1].ts, now) >= stale:
                        provider = await pick_provider(self.context, umo,
                                           self._cfg("provider_id", ""))
                        if provider is not None:
                            await self._seal(umo, provider, reason="静了")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[prism] 维护循环出错，继续")

    async def _flush_all(self) -> None:
        for umo in list(self.scene_book.open):
            try:
                provider = await pick_provider(self.context, umo,
                                           self._cfg("provider_id", ""))
                if provider is not None:
                    await self._seal(umo, provider, reason="退出")
            except Exception:
                logger.exception("[prism] 退出封存失败（%s）", umo)


    def _recent_others(self, umo: str, limit: int = 4) -> str:
        try:
            from .prism.state import _word_p
        except Exception:
            pass
        try:
            out = []
            scenes = sorted(self.memory.scenes.values(), key=lambda s: -s.end)
            for sc in scenes:
                if len(out) >= limit:
                    break
                for m in reversed(sc.moments or []):
                    if m.is_hers or not m.speaker:
                        continue
                    if getattr(m, "umo", "") == umo:
                        continue
                    if not (m.text or "").strip():
                        continue
                    who = m.speaker.split(":")[-1]


                    out.append("%s 那会儿跟我说过‘%s’"
                               % (_who_label(who, m.speaker), m.text[:30]))
                    if len(out) >= limit:
                        break
            if not out:
                return ""


            return "；".join(out) + "。"
        except Exception:
            logger.debug("[prism] 取最近他人对话失败", exc_info=True)
            return ""


    def read_context(self, *, umo: str = "", is_group: bool = False,
                     peer_name: str = "", group_name: str = "",
                     top_k: int = 4) -> str:
        """按共享设置提供心情、近期联系人与回忆；停用或暂停时返回空。"""
        try:
            if not self._enabled() or not runtime_control.allowed():
                return ""
            parts = []

            if self._cfg("share_mood", True):
                m = self.mood_state.describe()
                if m:


                    parts.append(m)


            if self._cfg("share_recent_others", True):
                recent = self._recent_others(umo, limit=4)
                if recent:


                    parts.append(recent)

            if self._cfg("share_recall", True):
                person = ""

                if umo and "FriendMessage" in umo:
                    uid = umo.split(":")[-1]
                    person = f"{_platform(umo)}:{uid}"
                text = self.recall_for(
                    person=person, is_group=is_group,
                    group_name=group_name, peer_name=peer_name,
                    top_k=top_k,
                )
                if text:
                    parts.append(text)

            out = "\n\n".join(parts)


            logger.info("[prism] 被读取：umo=%s group=%s → %d 字",
                        umo[-14:] if umo else "-", is_group, len(out))
            return out
        except Exception:
            logger.exception("[prism] read_context 出错，返回空")
            return ""

    def write_into_thought(self, *, umo: str = "", is_group: bool = False,
                           peer_name: str = "", group_name: str = "",
                           top_k: int = 4) -> str:
        try:
            if not self._enabled() or not runtime_control.allowed():
                return ""
            parts = []


            if self._cfg("share_mood", True):
                m = self.mood_state.describe()
                if m:
                    parts.append(m)


            if self._cfg("share_interperson", True):
                cur = ""
                if umo and "FriendMessage" in umo:
                    uid = umo.split(":")[-1]
                    cur = f"{_platform(umo)}:{uid}"
                if cur:
                    _st = self.interperson._state.get(cur) or {}
                    logger.info(
                        "[prism·対人·诊断] cur=%r 在库=%s n=%s keys=%s",
                        cur, bool(_st), _st.get("n"), list(self.interperson._state))
                    t = self.interperson.describe(
                        cur, name=peer_name or "", now=time.time())
                    logger.info("[prism·対人·诊断] describe 返回 %r", t)
                    if t:
                        parts.append(t)
                else:
                    logger.info("[prism·対人·诊断] umo=%r 拼不出 person（非私聊？）", umo)


            _ = (is_group, group_name, peer_name, top_k)
            out = "\n\n".join(parts)
            logger.info("[prism] 写进思考：umo=%s group=%s → %d 字",
                        umo[-14:] if umo else "-", is_group, len(out))
            return out
        except Exception:
            logger.exception("[prism] write_into_thought 出错，返回空")
            return ""

    def get_mood_state(self):
        return self.mood_state

    def last_relation_signals(self, *, umo: str = "") -> dict | None:
        try:
            out = {"valence": float(self.last_emotion_valence(umo=umo) or 0.0)}
            a = self._turn_appraisals.get(umo) if umo else self._last_appraisal
            if a is None:
                return out if abs(out["valence"]) > 1e-6 else None
            out["pleasant"] = float(max(-1.0, min(1.0, a.pleasantness)))
            out["fair"] = -1.0 if getattr(a, "fairness_touch", False) else 0.0
            out["fit"] = float(max(-1.0, min(1.0, a.internal_standards)))

            out["expect"] = float(max(0.0, min(1.0, a.expectation_discrepancy)))
            out.update(self._turn_relations.get(umo, {}))
            return out
        except Exception:
            logger.debug("[prism] last_relation_signals 出错", exc_info=True)
            return None

    def last_emotion_valence(self, *, umo: str = "") -> float | None:
        try:
            vals = []
            raw = self._turn_emotions.get(umo, "") if umo else self.mood_state.last_emotions
            for part in (raw or "").split("、"):
                part = part.strip()
                if not part:
                    continue

                i = len(part)
                while i > 0 and (part[i - 1].isdigit() or part[i - 1] == "."):
                    i -= 1
                kind, amp = part[:i].strip(), part[i:]
                if not kind:
                    continue
                try:
                    amp = float(amp)
                except ValueError:
                    continue
                pad = _emotion_pad(kind)
                if pad is None:
                    continue
                vals.append(float(pad[0]) * amp)
            if not vals:
                return None
            return sum(vals) / len(vals)
        except Exception:
            logger.debug("[prism] last_emotion_valence 出错", exc_info=True)
            return None

    def mood_text(self) -> str:
        try:
            if not self._cfg("share_mood", True):
                return ""
            return self.mood_state.describe()
        except Exception:
            return ""

    def recall_for(self, *, mood: Mood | None = None, person: str = "",
                   is_group: bool = False, group_name: str = "",
                   peer_name: str = "", top_k: int = 5,
                   reinforce: bool = False) -> str:
        try:
            if not self._enabled() or not runtime_control.allowed():
                return ""
            if not self._cfg("share_recall", True):
                return ""
            her = Her(
                mood=mood or self.mood,
                with_person=person,
                viewpoint=Viewpoint(is_group=is_group, group_name=group_name,
                                    peer_name=peer_name),
            )


            return remember(her, self.graph, self.memory,
                            top_k=top_k, reinforce=reinforce).render()
        except Exception:
            logger.exception("[prism] recall_for 出错，返回空")
            return ""

    def set_mood(self, p: float, a: float, d: float, reason: str = "") -> None:
        self.mood = Mood(p, a, d)
        self.mood_state = snapshot(self.mood, personality=self.personality)
        self._persist_mood()
        if reason:
            logger.info("[prism] 心情被手动设置为 %s（%s）", self.mood, reason)


    _MOOD_KEY = "mood_state"

    def _persist_mood(self) -> None:
        try:
            with self.store._db:
                self.store._db.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    (self._MOOD_KEY, self.mood_state.to_json()))
        except Exception:
            logger.debug("[prism] 心情落盘失败", exc_info=True)

    def _restore_mood(self) -> None:
        row = self.store._db.execute(
            "SELECT value FROM meta WHERE key=?", (self._MOOD_KEY,)).fetchone()
        if not row:
            return
        from .prism.state import read_state
        st = read_state(row["value"])
        if st.updated_at:
            self.mood_state = st
            self.mood = st.mood
            logger.info("[prism] 心情已恢复：%s", st.describe())


    @filter.command("棱镜")
    async def prism_cmd(self, event: AstrMessageEvent):
        """查看心情、召回当前经历或手动保存记忆结构。"""
        arg = (getattr(event, "message_str", "") or "").replace("/棱镜", "").strip()
        if arg.startswith("想"):
            text = self.recall_for(is_group="GroupMessage" in event.unified_msg_origin)
            return event.plain_result(text or "（此刻没想起什么）")
        if arg.startswith("存"):
            try:
                self.store.persist_from(self.graph, self.memory)
                return event.plain_result(f"已保存。{self.store.count()}")
            except Exception as e:
                return event.plain_result(f"保存失败：{e}")
        cnt = self.store.count()
        return event.plain_result(
            f"{self.mood_state.describe()}\n"
            f"场景 {cnt['scenes']} · 记忆 {cnt['traces']} · "
            f"节点 {cnt['nodes']} · 边 {cnt['edges']}"
        )


def _sender(event: AstrMessageEvent, default: str = "") -> str:
    try:
        return (event.get_sender_name() or "").strip() or default
    except Exception:
        return default


def _sender_id(event: AstrMessageEvent) -> str:
    try:
        return (event.get_sender_id() or "").strip()
    except Exception:
        return ""


_LIFE_TAG = re.compile(r"<(?:生活|life)>.*?</(?:生活|life)>", re.S)
_INTERACT_TAG = re.compile(r"<(?:互动|reply)>.*?</(?:互动|reply)>", re.S)
_REPLY_CONTENT = re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _strip_generated_tags(text: str) -> str:
    if not text:
        return ""


    m = _INTERACT_TAG.search(text)
    if m:
        cm = _REPLY_CONTENT.search(m.group(0))
        if cm:
            try:
                return json.loads('"' + cm.group(1) + '"').strip()
            except Exception:
                pass


    cleaned = _LIFE_TAG.sub("", text)
    cleaned = _INTERACT_TAG.sub("", cleaned)
    cleaned = re.sub(r"</?(?:生活|互动)>", "", cleaned)
    return cleaned.strip()


def _who_label(uid: str, person_id: str) -> str:
    return f"{uid}({person_id.split(':')[-1][-6:]})"


def _emotion_pad(kind: str):
    try:
        from .prism import affect as _af
        return (_af.EMOTION_TO_PAD.get(kind)
                or _af.EXTRA_EMOTIONS.get(kind))
    except Exception:
        return None


def _platform(umo: str) -> str:
    return (umo or "").split(":")[0]
