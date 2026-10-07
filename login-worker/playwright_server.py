"""Playwright headless 登录服务。

职责单一：接收账号密码 -> 在真实页面登录 -> 返回 localStorage 里的票据。
不落盘、不打印密码/token。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("login-worker")

LOGIN_URL = "https://u.ccb.com/sys/#/index"

app = FastAPI(title="CCB Login Worker")

_state: dict = {"pw": None, "browser": None, "lock": asyncio.Lock()}


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResult(BaseModel):
    token: str
    user_id: str = ""
    org_id: str = ""
    username: str = ""
    user_info: str = ""


@app.on_event("startup")
async def startup() -> None:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    )
    _state["pw"] = pw
    _state["browser"] = browser
    logger.info("playwright browser ready")


@app.on_event("shutdown")
async def shutdown() -> None:
    browser: Browser | None = _state.get("browser")
    pw = _state.get("pw")
    if browser:
        await browser.close()
    if pw:
        await pw.stop()


async def _do_login(username: str, password: str) -> dict:
    browser: Browser = _state["browser"]
    context = await browser.new_context(
        viewport={"width": 1366, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        # 等登录表单出现（未登录会跳转/渲染登录框）
        await page.wait_for_selector("#inputAcc", timeout=30000)
        await page.fill("#inputAcc", username)
        await page.fill("#inputPwd", password)
        await page.click("#btnLoginInner")

        # 等待拿到票据：轮询 localStorage.token
        token = ""
        for _ in range(30):  # 最长约 30s
            await page.wait_for_timeout(1000)
            try:
                token = await page.evaluate("() => localStorage.getItem('token') || ''")
            except Exception:
                token = ""
            if token.startswith("ticket-2-"):
                break

        if not token.startswith("ticket-2-"):
            url = page.url
            raise RuntimeError(f"登录未成功，当前 URL: {url}")

        # SPA 渲染滞后，再等几秒读全身份信息
        await page.wait_for_timeout(5000)

        ls = await page.evaluate(
            """() => ({
                token: localStorage.getItem('token') || '',
                userId: localStorage.getItem('userId') || '',
                orgId: localStorage.getItem('orgId') || '',
                username: localStorage.getItem('username') || '',
                CU_USERINFO: localStorage.getItem('CU_USERINFO') || ''
            })"""
        )

        if not ls["token"].startswith("ticket-2-"):
            raise RuntimeError("登录后票据异常")

        return {
            "token": ls["token"],
            "user_id": ls["userId"],
            "org_id": ls["orgId"],
            "username": ls["username"],
            "user_info": ls["CU_USERINFO"],
        }
    finally:
        await context.close()


@app.post("/login", response_model=LoginResult)
async def login(req: LoginRequest) -> LoginResult:
    # 凭证只来自本次请求体，登录后不写盘、不保留；服务重启即失效。
    username = (req.username or "").strip()
    password = req.password or ""  # 不 strip 密码
    if not username or not password:
        raise RuntimeError("用户名/密码不能为空")
    # 串行化登录，避免并发起多个浏览器上下文触发风控
    async with _state["lock"]:
        try:
            data = await _do_login(username, password)
        except Exception as exc:
            logger.warning("login failed: %s", exc)
            raise
    logger.info("login success for user, token prefix=%s...", data["token"][:12])
    return LoginResult(**data)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "browser_ready": _state.get("browser") is not None}
