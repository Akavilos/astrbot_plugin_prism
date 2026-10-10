"""核对模型关系评分的原文证据与同一发言人历史证据，形成各维度权重。"""

import math

DIMENSIONS = ("liking", "reliability", "honesty", "benevolence")


def read_relationship(text: str, payload: dict, history: list[str] | None = None) -> dict:
    """只接受发言者评分和匹配到原话的证据；支持的历史证据增加该项权重。"""
    raw = payload.get("relationship") or {}
    result = {key: 0.0 for key in DIMENSIONS}
    result["evidence"] = {}
    result["context_evidence"] = {}
    result["evidence_weight"] = {}
    confidence = float(payload.get("confidence", 0.0))
    result["confidence"] = min(1.0, max(0.0, confidence)) if math.isfinite(confidence) else 0.0
    if not isinstance(raw, dict) or raw.get("actor") != "sender":
        return result
    current = "".join(text.split())
    for key in DIMENSIONS:
        item = raw.get(key)
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            continue
        matches = [s.strip() for s in evidence if isinstance(s, str)
                   and s.strip() and "".join(s.split()) in current]
        value = item.get("score", 0.0)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        if matches:
            result[key] = min(1.0, max(-1.0, value))
            result["evidence"][key] = matches[:2]

            context = item.get("context_evidence") or []
            supported = [s.strip() for s in context if isinstance(s, str) and s.strip()
                         and any("".join(s.split()) in "".join(h.split())
                                 for h in (history or [])
                                 if h.startswith(("[同一发言人]", "[我]")))] if isinstance(context, list) else []
            result["context_evidence"][key] = supported[:2]
            result["evidence_weight"][key] = 0.5 if supported else 0.25
    return result
