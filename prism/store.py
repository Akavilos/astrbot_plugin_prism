"""以SQLite保存场景、记忆痕迹、联想图和人物信息。
整批保存时在同一事务中写入场景、痕迹与图结构。"""


from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from astrbot.api import logger


SCHEMA_VERSION = 1

_SCHEMA = """
 
-- 持久化表与索引定义；字段名称供存储接口及既有数据读取使用。
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS scenes (
    scene_id  TEXT PRIMARY KEY,
    start     REAL NOT NULL,
    payload   TEXT NOT NULL,           
    updated   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scenes_start ON scenes(start);


CREATE TABLE IF NOT EXISTS traces (
    key       TEXT PRIMARY KEY,
    encoded_at REAL NOT NULL,
    payload   TEXT NOT NULL,           
    updated   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_encoded ON traces(encoded_at);


CREATE TABLE IF NOT EXISTS nodes (
    key     TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    a       TEXT NOT NULL,
    b       TEXT NOT NULL,
    weight  REAL NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS idx_edges_a ON edges(a);


CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    payload   TEXT NOT NULL,
    updated   REAL NOT NULL
);
"""


class PrismStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._db.commit()


    @staticmethod
    def _mood(m) -> list:
        return [round(float(m.p), 4), round(float(m.a), 4), round(float(m.d), 4)]

    @staticmethod
    def _load_mood(raw, default=None):
        from .affect import Mood
        try:
            p, a, d = (raw or [0.0, 0.0, 0.0])[:3]
            return Mood(float(p), float(a), float(d))
        except Exception:
            return default or Mood()

    @staticmethod
    def _moment_to_dict(m) -> dict:
        return {
            "ts": m.ts, "speaker": m.speaker, "text": m.text,
            "is_hers": bool(m.is_hers), "mood": PrismStore._mood(m.mood),
            "emotions": [list(x) for x in (m.emotions or ())],
            "salience": m.salience, "umo": m.umo,
            "event": m.event, "topic": m.topic,
        }

    @staticmethod
    def _dict_to_moment(d: dict):
        from .scene import Moment
        return Moment(
            ts=float(d.get("ts") or 0.0),
            speaker=str(d.get("speaker") or ""),
            text=str(d.get("text") or ""),
            is_hers=bool(d.get("is_hers")),
            mood=PrismStore._load_mood(d.get("mood")),
            emotions=tuple((str(x[0]), float(x[1]))
                           for x in (d.get("emotions") or []) if len(x) >= 2),
            salience=float(d.get("salience") or 0.0),
            umo=str(d.get("umo") or ""),
            event=str(d.get("event") or ""),
            topic=str(d.get("topic") or ""),
        )

    @classmethod
    def scene_to_json(cls, sc) -> str:
        from .decay import Revision
        d = {
            "scene_id": sc.scene_id, "start": sc.start, "end": sc.end,
            "present": dict(sc.present or {}),
            "sessions": sorted(sc.sessions or []),
            "moments": [cls._moment_to_dict(m) for m in (sc.moments or [])],
            "peak_salience": sc.peak_salience,
            "peak_mood": cls._mood(sc.peak_mood),
            "peak_emotions": [list(x) for x in (sc.peak_emotions or ())],
            "peak_text": sc.peak_text,
            "mood_at_start": cls._mood(sc.mood_at_start),
            "felt_about": dict(sc.felt_about or {}),

            "narrative": sc.narrative, "doing": sc.doing,
            "noticed": list(sc.noticed or []),
            "not_noticed": list(sc.not_noticed or []),
            "about_others": dict(sc.about_others or {}),
            "meaning": sc.meaning,

            "revisions": [{"key": r.key, "before": r.before, "after": r.after,
                           "reason": r.reason, "at": r.at}
                          for r in (sc.revisions or [])],
            "public": bool(sc.public),
        }
        _ = Revision
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def json_to_scene(cls, raw: str):
        from .decay import Revision
        from .scene import Scene
        try:
            d = json.loads(raw)
            if not isinstance(d, dict):
                return None
        except Exception:
            return None
        try:
            sc = Scene(
                scene_id=str(d.get("scene_id") or ""),
                start=float(d.get("start") or 0.0),
                end=float(d.get("end") or 0.0),
                present=dict(d.get("present") or {}),
                sessions=set(d.get("sessions") or []),
                moments=[cls._dict_to_moment(x) for x in (d.get("moments") or [])
                         if isinstance(x, dict)],
                peak_salience=float(d.get("peak_salience") or 0.0),
                peak_mood=cls._load_mood(d.get("peak_mood")),
                peak_emotions=tuple((str(x[0]), float(x[1]))
                                    for x in (d.get("peak_emotions") or [])
                                    if isinstance(x, list) and len(x) >= 2),
                peak_text=str(d.get("peak_text") or ""),
                mood_at_start=cls._load_mood(d.get("mood_at_start")),
                felt_about={k: float(v) for k, v in (d.get("felt_about") or {}).items()},
                narrative=str(d.get("narrative") or ""),
                doing=str(d.get("doing") or ""),
                noticed=[str(x) for x in (d.get("noticed") or [])],
                not_noticed=[str(x) for x in (d.get("not_noticed") or [])],
                about_others={str(k): str(v)
                              for k, v in (d.get("about_others") or {}).items()},
                meaning=str(d.get("meaning") or ""),
                public=bool(d.get("public")),
            )
            for r in (d.get("revisions") or []):
                if isinstance(r, dict):
                    sc.revisions.append(Revision(
                        key=str(r.get("key") or ""), before=str(r.get("before") or ""),
                        after=str(r.get("after") or ""), reason=str(r.get("reason") or ""),
                        at=float(r.get("at") or 0.0)))
            return sc
        except Exception:
            logger.warning("[prism] 场景反序列化失败，跳过这一条", exc_info=False)
            return None

    @staticmethod
    def trace_to_json(t) -> str:
        return json.dumps({
            "key": t.key, "strength": t.strength, "encoded_at": t.encoded_at,
            "arousal_at_encode": t.arousal_at_encode, "salience": t.salience,
            "rehearsals": t.rehearsals, "last_recalled": t.last_recalled,
        }, ensure_ascii=False)

    @staticmethod
    def json_to_trace(raw: str):
        from .decay import Trace
        try:
            d = json.loads(raw)
            return Trace(
                key=str(d.get("key") or ""),
                strength=float(d.get("strength") or 1.0),
                encoded_at=float(d.get("encoded_at") or 0.0),
                arousal_at_encode=float(d.get("arousal_at_encode") or 0.0),
                salience=float(d.get("salience") or 0.0),
                rehearsals=int(d.get("rehearsals") or 0),
                last_recalled=float(d.get("last_recalled") or 0.0),
            )
        except Exception:
            return None


    def save_scene(self, sc, now: float | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO scenes(scene_id, start, payload, updated) "
                "VALUES(?,?,?,?)",
                (sc.scene_id, float(sc.start), self.scene_to_json(sc),
                 now or time.time()),
            )

    def save_trace(self, t, now: float | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO traces(key, encoded_at, payload, updated) "
                "VALUES(?,?,?,?)",
                (t.key, float(t.encoded_at), self.trace_to_json(t), now or time.time()),
            )

    def save_graph(self, graph, now: float | None = None) -> None:
        nodes, edges = self._graph_rows(graph)
        with self._db:
            self._replace_graph(nodes, edges)

    @staticmethod
    def _graph_rows(graph) -> tuple[list, list]:
        nodes = [(n.key, n.kind, json.dumps(n.data, ensure_ascii=False))
                 for n in graph.nodes.values()]
        seen, edges = set(), []
        for a, neighbors in graph.edges.items():
            for b, weight in neighbors.items():
                key = tuple(sorted((a, b)))
                if key not in seen:
                    seen.add(key)
                    edges.append((a, b, float(weight)))
        return nodes, edges

    def _replace_graph(self, nodes: list, edges: list) -> None:
        self._db.execute("DELETE FROM nodes")
        self._db.execute("DELETE FROM edges")
        self._db.executemany(
            "INSERT OR REPLACE INTO nodes(key, kind, payload) VALUES(?,?,?)", nodes)
        self._db.executemany(
            "INSERT OR REPLACE INTO edges(a, b, weight) VALUES(?,?,?)", edges)

    def save_person(self, person_id: str, payload: dict,
                    now: float | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO persons(person_id, payload, updated) "
                "VALUES(?,?,?)",
                (person_id, json.dumps(payload, ensure_ascii=False), now or time.time()),
            )


    def load_scene(self, scene_id: str):
        row = self._db.execute(
            "SELECT payload FROM scenes WHERE scene_id=?", (scene_id,)).fetchone()
        return self.json_to_scene(row["payload"]) if row else None

    def load_scenes(self, limit: int = 0) -> list:
        sql = "SELECT payload FROM scenes ORDER BY start"
        if limit:
            sql += f" LIMIT {int(limit)}"
        out = []
        for row in self._db.execute(sql):
            sc = self.json_to_scene(row["payload"])
            if sc is not None:
                out.append(sc)
        return out

    def count(self) -> dict:
        c = lambda t: self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return {"scenes": c("scenes"), "traces": c("traces"),
                "nodes": c("nodes"), "edges": c("edges"), "persons": c("persons")}

    def load_traces(self) -> list:
        out = []
        for row in self._db.execute("SELECT payload FROM traces ORDER BY encoded_at"):
            t = self.json_to_trace(row["payload"])
            if t is not None:
                out.append(t)
        return out

    def list_person_ids(self) -> list[str]:
        return [r["person_id"] for r in self._db.execute(
            "SELECT person_id FROM persons")]

    def load_person(self, person_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT payload FROM persons WHERE person_id=?", (person_id,)).fetchone()
        if not row:
            return None
        try:
            d = json.loads(row["payload"])
            return d if isinstance(d, dict) else None
        except Exception:
            return None


    def load_into(self, graph, memory_store) -> dict:
        scenes = self.load_scenes()
        for sc in scenes:
            graph.add_scene(sc)
            memory_store.add(sc, now=sc.start)

        for t in self.load_traces():
            memory_store.traces[t.key] = t


        for row in self._db.execute("SELECT a, b, weight FROM edges"):
            graph.link(row["a"], row["b"], 0.0)
            if row["a"] in graph.edges and row["b"] in graph.nodes:
                graph.edges[row["a"]][row["b"]] = float(row["weight"])
                graph.edges.setdefault(row["b"], {})[row["a"]] = float(row["weight"])
        graph.finalize_people_edges()

        cnt = self.count()
        logger.info("[prism] 从库载入：%s", cnt)
        return cnt

    def persist_from(self, graph, memory_store, now: float | None = None) -> int:
        """在同一事务中保存场景与痕迹，并替换联想图节点和连接。"""
        at = now or time.time()
        scenes = [(sc.scene_id, float(sc.start), self.scene_to_json(sc), at)
                  for sc in memory_store.scenes.values()]
        traces = [(trace.key, float(trace.encoded_at), self.trace_to_json(trace), at)
                  for trace in memory_store.traces.values()]
        nodes, edges = self._graph_rows(graph)

        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO scenes(scene_id, start, payload, updated) VALUES(?,?,?,?)",
                scenes)
            self._db.executemany(
                "INSERT OR REPLACE INTO traces(key, encoded_at, payload, updated) VALUES(?,?,?,?)",
                traces)
            self._replace_graph(nodes, edges)
        return len(scenes)

    def backup(self, dest: str | Path) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(dest)) as out:
            self._db.backup(out)

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
