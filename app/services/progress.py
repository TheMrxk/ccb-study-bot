"""pointListMap 生成与单视频刷课状态机。"""
from __future__ import annotations

import logging
import math
import random
from typing import Awaitable, Callable

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

POINT_SIZE = 5  # 真机点位粒度：每 5 秒一个点位


def build_point_map(video_len: float, view_schedule: float, size: int = POINT_SIZE) -> list[int]:
    """已跨过的整 size 秒区间置 1。"""
    n = max(1, math.ceil(video_len / size))
    return [1 if view_schedule >= (i + 1) * size else 0 for i in range(n)]


def next_heartbeat() -> float:
    span = float(random.randint(settings.heartbeat_min, settings.heartbeat_max))
    if settings.jitter:
        span += random.uniform(-3, 3)
    return max(10.0, span)


# 控制信号回调：返回 False 表示暂停/停止
ControlFn = Callable[[], Awaitable[bool]]


async def run_video(
    *,
    client_factory: Callable[[], Awaitable["object"]],
    knowledge_id: str,
    package_id: str,
    user_knowledge_id: str,
    master_id: str,
    parent_id: str,
    title: str,
    duration: float,
    start_seconds: float,
    speed: float = 1.0,
    on_progress: Callable[[float, bool | None, str], Awaitable[None]],
    is_running: ControlFn,
) -> None:
    """单视频状态机。遇 401 抛 ApiError 交 runner 重新登录后续跑。"""
    from services.ccb_api import ApiError, CcbClient

    client: CcbClient = await client_factory()
    try:
        # 1. 进入播放记录
        try:
            await client.enter_study(knowledge_id, package_id, parent_id, title)
        except ApiError as exc:
            if exc.status_code == 401:
                raise
            logger.warning("enter_study 非致命失败: %s", exc)

        view = min(float(start_seconds or 0), duration)
        times = 1

        while view < duration:
            if not await is_running():
                await on_progress(view, None, "paused")
                return

            span = next_heartbeat()
            view = min(duration, view + span)
            points = build_point_map(duration, view)

            # 上报，指数退避；401 上抛由 runner 刷新 token
            last_err: Exception | None = None
            for attempt in range(3):
                if not await is_running():
                    await on_progress(view, None, "paused")
                    return
                try:
                    await client.submit_schedule(
                        knowledge_id, package_id, user_knowledge_id, master_id,
                        view, span, times, points,
                    )
                    last_err = None
                    break
                except ApiError as exc:
                    if exc.status_code == 401:
                        raise
                    last_err = exc
                    await asyncio_sleep(2 ** attempt + random.uniform(0, 1))
            if last_err is not None:
                raise last_err

            await on_progress(view, False, f"上报第 {times} 次")
            times += 1

            # 倍速等待：真实等待 = 心跳推进 / 倍率（1x时与真实看片一致）
            if view < duration:
                wait = min(span, settings.heartbeat_max) / max(1.0, float(speed))
                await asyncio_sleep(max(3.0, wait))

        # 2. 终值校验 + 补报。只有建行回执确认学时真达标才标 done，
        # 否则保持未完成（记录原因），杜绝本地假done。
        verified = await _verify(
            client, knowledge_id, user_knowledge_id, package_id, master_id, duration
        )
        if verified:
            await on_progress(duration, True, "已完成")
        else:
            await on_progress(view, False, "已上报但建行未确认满分，待重刷")
    finally:
        await client.aclose()


async def _verify(
    client, knowledge_id: str, user_knowledge_id: str,
    package_id: str, master_id: str, duration: float,
) -> bool:
    from services.ccb_api import ApiError

    for _ in range(3):
        try:
            prog = await client.query_progress(knowledge_id, user_knowledge_id)
        except ApiError:
            return False
        server_view = _parse_float(prog.get("viewScheduleLine"))
        if server_view >= duration - 1.0:
            return True
        points = build_point_map(duration, duration)
        try:
            await client.submit_schedule(
                knowledge_id, package_id, user_knowledge_id, master_id,
                duration, 0, 999, points,
            )
        except ApiError:
            return False
    return False


def _parse_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(max(0.0, seconds))
