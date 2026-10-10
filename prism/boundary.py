"""判断连续往来是否属于同一场景，结合静默间隔与模型判断确定边界。"""


from __future__ import annotations

from . import runtime as runtime_mod

import json
import re

from astrbot.api import logger

import time


BOUNDARY_CONFIDENCE = 0.65


MAX_BUFFER = 24


HARD_GAP_SECONDS = 30 * 60


_SYSTEM = """我在判断这段聊天里，刚到的这两三句话和前面是不是同一件事。

我只输出一个 JSON 对象，不加 markdown 围栏和解释。

{"same": true 或 false, "confidence": 0.0~1.0}

判断标准：
- **同一件事**：接着刚才的话题往下说、补充、追问、应答、吐槽同一件事。
- **不是同一件事**：话题中心换了（从聊活动 → 聊抽奖 → 聊谁没来）。

🔴 注意：
· **措辞不同不等于换了话题。** "抽奖结果不好" 和 "奖池有保底"
  我把它们理解为同一件事（抽奖），不单凭用词不同就判成换了话题。
· 我拿不准时填 same=true，保留经历的连续性。
· confidence 是我对判断的信心，不是话题变化的大小。"""


def _build_user_msg(prev_lines: list[str], cur_lines: list[str]) -> str:
    prev = "\n".join(prev_lines[-6:]) or "（这是最开始）"
    cur = "\n".join(cur_lines[-4:])
    return (f"【前面在聊的】\n{prev}\n\n"
            f"【刚到的】\n{cur}\n\n"
            f"这两段是同一件事吗？")


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_verdict(raw: str) -> tuple[bool, float] | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    d = None
    try:
        d = json.loads(s)
    except Exception:
        m = _JSON_RE.search(s)
        if m:
            try:
                d = json.loads(m.group(0))
            except Exception:
                d = None
    if not isinstance(d, dict):
        return None
    same = d.get("same")
    if isinstance(same, str):
        same = same.strip().lower() in ("true", "yes", "1", "是")
    if not isinstance(same, bool):
        return None
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return same, conf


async def judge_async(provider, prev_lines: list[str], cur_lines: list[str],
                      *, max_tokens: int = 20000) -> tuple[bool, float]:
    if not cur_lines:
        return True, 0.0
    try:
        resp = await runtime_mod.call_model(provider,
            prompt=_build_user_msg(prev_lines, cur_lines),
            system_prompt=_SYSTEM,
            temperature=0.1,

            max_tokens=max_tokens,
        )
        raw = getattr(resp, "completion_text", "") or ""
        v = parse_verdict(raw)
        if v is None:
            logger.warning("[prism] 边界判断无法解析，保守判为同一件事。前 100 字：%s", raw[:100])
            return True, 0.0
        return v
    except Exception as error:
        runtime_mod.record_failure("model", error)
        logger.exception("[prism] 边界判断失败，保守判为同一件事")
        return True, 0.0


def hard_boundary(prev, cur) -> str:
    gap = cur.ts - prev.ts
    if gap >= HARD_GAP_SECONDS:
        return f"静默 {gap/60:.0f} 分钟"
    return ""


def should_split(same: bool, confidence: float, *,
                 threshold: float = BOUNDARY_CONFIDENCE) -> bool:
    """仅在判断为不同场景且置信度达到门槛时切分。"""
    return (not same) and confidence >= threshold
