"""注册棱镜的数据查询与单项编辑接口，管理心情、场景、强度和联想图。"""


from __future__ import annotations

import json
import time

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("prism")


PREFIX = "/astrbot_plugin_prism/page"


class PageApi:
    def __init__(self, plugin) -> None:
        self.plugin = plugin


    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        p = PREFIX


        register(f"{p}/overview", self.overview, ["GET"], "棱镜概览")
        register(f"{p}/mood", self.get_mood, ["GET"], "当前心情（跨插件共享的那份）")
        register(f"{p}/mood/set", self.set_mood, ["POST"], "手动设置心情")


        register(f"{p}/scenes", self.list_scenes, ["GET"], "场景列表")
        register(f"{p}/scenes/detail", self.scene_detail, ["GET"], "场景详情")
        register(f"{p}/scenes/update", self.update_scene, ["POST"], "编辑场景叙事")
        register(f"{p}/scenes/delete", self.delete_scene, ["POST"], "删除场景")


        register(f"{p}/traces", self.list_traces, ["GET"], "记忆强度列表")
        register(f"{p}/traces/boost", self.boost_trace, ["POST"], "手动加固一条记忆")


        register(f"{p}/graph", self.get_graph, ["GET"], "联想图（节点+边）")
        register(f"{p}/graph/neighbors", self.get_neighbors, ["GET"], "某个节点的邻居")


        register(f"{p}/persons", self.list_persons, ["GET"], "人物列表")


    def _store(self):
        return self.plugin.store

    def _runtime(self):
        return self.plugin.graph, self.plugin.memory


    @staticmethod
    def _ok(data) -> dict:
        return {"ok": True, "data": data}

    @staticmethod
    def _err(msg: str) -> dict:
        return {"ok": False, "error": msg}


    async def overview(self):
        try:
            graph, memory = self._runtime()
            st = self._store()
            cnt = st.count() if st else {}
            scene_list = list(memory.scenes.values())
            narr = sum(1 for s in scene_list if getattr(s, "narrative", "").strip())
            alive = len(memory.alive()) if hasattr(memory, "alive") else len(scene_list)
            return self._ok({
                "scenes": cnt.get("scenes", len(scene_list)),
                "scenes_with_narrative": narr,
                "traces_alive": alive,
                "traces_forgotten": max(0, len(memory.traces) - alive),
                "graph_nodes": len(graph.nodes),
                "graph_edges": sum(len(v) for v in graph.edges.values()) // 2,
                "persons": cnt.get("persons", 0),
                "schema_version": cnt and 1,
            })
        except Exception as e:
            logger.exception("[prism] overview 出错")
            return self._err(str(e))


    async def get_mood(self):
        try:
            ms = getattr(self.plugin, "mood_state", None)
            if ms is None:
                return self._ok({"mood": [0.0, 0.0, 0.0], "describe": "（还没建立）"})
            return self._ok({
                "mood": [ms.mood.p, ms.mood.a, ms.mood.d],
                "describe": ms.describe(),
                "updated_at": ms.updated_at,
                "stale": ms.stale(),
                "last_emotions": ms.last_emotions,
            })
        except Exception as e:
            return self._err(str(e))

    async def set_mood(self):
        try:
            from quart import request
            body = await request.get_json(silent=True) or {}
            p = max(-1.0, min(1.0, float(body.get("p", 0))))
            a = max(-1.0, min(1.0, float(body.get("a", 0))))
            d = max(-1.0, min(1.0, float(body.get("d", 0))))
            self.plugin.set_mood(p, a, d, reason="面板手动设置")
            return self._ok({"mood": [p, a, d]})
        except Exception as e:
            return self._err(str(e))


    async def list_scenes(self):
        try:
            _graph, memory = self._runtime()
            out = []
            for sc in sorted(memory.scenes.values(), key=lambda s: -s.start):
                out.append({
                    "scene_id": sc.scene_id,
                    "start": sc.start,
                    "time": _fmt(sc.start),
                    "people": len(sc.present or {}),
                    "sessions": len(sc.sessions or ()),
                    "moments": len(sc.moments or []),
                    "peak_salience": round(sc.peak_salience, 3),
                    "public": bool(sc.public),
                    "has_narrative": bool(getattr(sc, "narrative", "").strip()),
                    "narrative": (getattr(sc, "narrative", "") or "")[:60],
                })
            return self._ok(out)
        except Exception as e:
            return self._err(str(e))

    async def scene_detail(self):
        try:
            from quart import request
            sid = (await request.args).get("scene_id", "")
            _graph, memory = self._runtime()
            sc = memory.scenes.get(f"scene:{sid}") or memory.scenes.get(sid)
            if sc is None:
                return self._err(f"找不到场景 {sid}")
            return self._ok({
                "scene_id": sc.scene_id,
                "start": sc.start, "end": sc.end, "time": _fmt(sc.start),
                "present": list((sc.present or {}).keys()),
                "sessions": sorted(sc.sessions or []),
                "public": bool(sc.public),
                "peak_salience": sc.peak_salience,
                "peak_mood": [sc.peak_mood.p, sc.peak_mood.a, sc.peak_mood.d],
                "peak_emotions": [list(x) for x in (sc.peak_emotions or ())],
                "peak_text": sc.peak_text,

                "narrative": sc.narrative,
                "doing": sc.doing,
                "noticed": list(sc.noticed or []),
                "not_noticed": list(sc.not_noticed or []),
                "about_others": dict(sc.about_others or {}),
                "meaning": sc.meaning,

                "revisions": [{"before": r.before, "after": r.after,
                               "reason": r.reason, "at": _fmt(r.at)}
                              for r in (sc.revisions or [])],
                "moments": [{"time": _fmt(m.ts), "speaker": m.speaker,
                             "is_hers": m.is_hers, "text": m.text,
                             "salience": round(m.salience, 3), "topic": m.topic}
                            for m in (sc.moments or [])],
            })
        except Exception as e:
            return self._err(str(e))

    async def update_scene(self):
        try:
            from quart import request
            body = await request.get_json(silent=True) or {}
            sid = str(body.get("scene_id", "")).strip()
            _graph, memory = self._runtime()
            sc = memory.scenes.get(f"scene:{sid}") or memory.scenes.get(sid)
            if sc is None:
                return self._err(f"找不到场景 {sid}")

            changed = []
            for field in ("narrative", "doing", "meaning"):
                if field in body and body[field] is not None:
                    old = getattr(sc, field, "")
                    new = str(body[field])
                    if old != new:
                        changed.append(field)
                        setattr(sc, field, new)

            if changed and hasattr(sc, "revisions"):
                from .decay import Revision
                sc.revisions.append(Revision(
                    key=f"scene:{sc.scene_id}",
                    before="（面板修改）", after="、".join(changed),
                    reason="管理面板手动修改", at=time.time(),
                ))


            st = self._store()
            if st:
                st.save_scene(sc)
            return self._ok({"changed": changed})
        except Exception as e:
            return self._err(str(e))

    async def delete_scene(self):
        try:
            from quart import request
            body = await request.get_json(silent=True) or {}
            sid = str(body.get("scene_id", "")).strip()
            key = f"scene:{sid}"
            _graph, memory = self._runtime()
            memory.scenes.pop(key, None)
            memory.traces.pop(key, None)
            st = self._store()
            if st:
                with st._db:
                    st._db.execute("DELETE FROM scenes WHERE scene_id=?", (sid,))
                    st._db.execute("DELETE FROM traces WHERE key=?", (key,))
            return self._ok({"deleted": sid})
        except Exception as e:
            return self._err(str(e))


    async def list_traces(self):
        try:
            _graph, memory = self._runtime()
            now = time.time()
            out = []
            for key, tr in memory.traces.items():
                sc = memory.scenes.get(key)
                out.append({
                    "key": key,
                    "scene_id": key.split(":", 1)[-1],
                    "strength": round(tr.current(now), 4),
                    "rehearsals": tr.rehearsals,
                    "forgotten": tr.forgotten(now),
                    "encoded_at": tr.encoded_at,
                    "time": _fmt(tr.encoded_at),
                    "salience": round(tr.salience, 3),
                    "text": (getattr(sc, "narrative", "") or "")[:50] if sc else "",
                })
            out.sort(key=lambda x: -x["strength"])
            return self._ok(out)
        except Exception as e:
            return self._err(str(e))

    async def boost_trace(self):
        try:
            from quart import request
            body = await request.get_json(silent=True) or {}
            key = str(body.get("key", "")).strip()
            times = max(1, min(20, int(body.get("times", 1))))
            _graph, memory = self._runtime()
            tr = memory.traces.get(key)
            if tr is None:
                return self._err(f"找不到 {key}")
            for _ in range(times):
                tr.recall()
            st = self._store()
            if st:
                st.save_trace(tr)
            return self._ok({"key": key, "rehearsals": tr.rehearsals,
                             "strength": round(tr.current(), 4)})
        except Exception as e:
            return self._err(str(e))


    async def get_graph(self):
        try:
            graph, _memory = self._runtime()
            nodes = [{"id": n.key, "kind": n.kind,
                      "label": n.data.get("narrative", "")[:20] or n.key.split(":")[-1][:14],
                      "salience": n.data.get("peak_salience", 0)}
                     for n in graph.nodes.values()]
            seen, edges = set(), []
            for a, nbrs in graph.edges.items():
                for b, w in nbrs.items():
                    k = tuple(sorted((a, b)))
                    if k in seen:
                        continue
                    seen.add(k)
                    edges.append({"a": a, "b": b, "w": round(w, 3)})
            return self._ok({"nodes": nodes, "edges": edges})
        except Exception as e:
            return self._err(str(e))

    async def get_neighbors(self):
        try:
            from quart import request
            key = (await request.args).get("key", "")
            graph, _memory = self._runtime()
            nb = graph.neighbors(key)
            return self._ok([{"key": k, "w": round(w, 3),
                              "kind": graph.nodes[k].kind if k in graph.nodes else "?"}
                             for k, w in sorted(nb.items(), key=lambda x: -x[1])])
        except Exception as e:
            return self._err(str(e))


    async def list_persons(self):
        try:
            graph, memory = self._runtime()
            out = []
            for node in graph.nodes.values():
                if node.kind != "person":
                    continue
                nb = graph.neighbors(node.key)
                scenes = [k for k in nb if k.startswith("scene:")]
                people = [k for k in nb if k.startswith("person:")]
                out.append({
                    "person_id": node.key.split("person:", 1)[-1],
                    "scenes": len(scenes),
                    "acquainted_with": [k.split("person:")[-1] for k in people],
                })
            out.sort(key=lambda x: -x["scenes"])
            return self._ok(out)
        except Exception as e:
            return self._err(str(e))


def _fmt(ts: float) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except Exception:
        return "?"
