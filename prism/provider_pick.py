"""优先选取明确配置的模型提供商，再尝试当前会话的框架提供商。"""


from __future__ import annotations

from astrbot.api import logger


async def pick(context, umo: str, provider_id: str = ""):
    pid = (provider_id or "").strip()

    if pid:
        try:

            prov = context.get_provider_by_id(pid)
            if prov is not None:
                return prov
            logger.warning(
                "[prism] 配置的 provider %r 没找到，退回默认。"
                "（面板里 id 要对得上，比如 `deepseek-reason/deepseek-flash`）", pid)
        except Exception:
            logger.exception("[prism] 按 id 取 provider 失败，退回默认")

    try:
        return await context.get_using_provider_async(umo)
    except Exception:
        logger.exception("[prism] 连默认 provider 都拿不到")
        return None
