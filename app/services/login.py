"""调用 login-worker 完成登录 / 刷新 token。凭证只在请求内传递。"""
from __future__ import annotations

import logging

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LoginError(RuntimeError):
    pass


async def fetch_token(username: str, password: str) -> dict:
    """向 playwright 登录服务提交账号密码换取票据。

    返回 {token, user_id, org_id, username, user_info}
    """
    username = (username or "").strip()
    if not username or not password:
        raise LoginError("用户名/密码不能为空")

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{settings.login_worker_url}/login",
            json={"username": username, "password": password},
        )
    if resp.status_code != 200:
        # 尝试提取错误信息
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise LoginError(f"登录失败({resp.status_code}): {detail}")
    data = resp.json()
    logger.info("已获取票据 %s...", data["token"][:12])
    return data
