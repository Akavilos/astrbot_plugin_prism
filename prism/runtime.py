"""连接可选的共同运行控制器，在模型调用与结果提交处检查暂停代次。
没有控制器时独立运行，间隔计算使用实际时间。"""

import time


class RuntimePaused(RuntimeError):
    _spectrum_expected_skip = True


async def call_model(provider, **kwargs):
    """调用前后核对共同控制器代次，错误登记后继续抛给调用方。"""
    token = checkpoint()
    if not allowed(token):
        raise RuntimePaused("棱镜模型请求暂停")
    try:
        response = await provider.text_chat(**kwargs)
        if getattr(response, "role", "") == "err":
            raise RuntimeError("供应商返回模型错误结果")
    except Exception as error:
        record_failure("model", error)
        raise
    if not allowed(token):
        raise RuntimePaused("棱镜模型结果跨过暂停周期")
    return response


def controller():
    """从框架注册对象查找运行控制接口，没有接口则独立运行。"""
    try:
        from astrbot.core.star.star import star_map
    except ImportError:
        return None
    for meta in star_map.values():
        plugin = getattr(meta, "star_cls", None)
        control = getattr(plugin, "runtime_control", None)
        if control is not None:
            return control
    return None


def checkpoint():
    control = controller()
    return control, control.checkpoint() if control else 0


def allowed(token=None) -> bool:
    """控制器未冻结且代次相符时允许提交；未接入时按独立运行处理。"""
    control = controller()
    if control is None:
        return token is None or token[0] is None
    if control.is_frozen():
        return False
    return token is None or token[0] is None or (token[0] is control and control.may_commit(token[1]))


def elapsed_since(start: float, now: float | None = None) -> float:
    control = controller()
    if control is not None:
        return control.elapsed_since(start, now)
    return max(0.0, (time.time() if now is None else now) - start)


def record_failure(source: str, error: Exception) -> None:
    control = controller()
    if control is not None:
        control.record_failure("prism:" + source, error)
