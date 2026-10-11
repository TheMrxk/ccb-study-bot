"""FastAPI 入口：API 路由 + Web 页面。

身份：建行用户名即唯一身份；不设面板账号。
当前登录建行账号保存在 runner.current_ccb，课程/视频按 owner_ccb 隔离。
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from database import SessionLocal, init_db
from models import Announcement, Course, StudyItem, Video
from announcements import ANNOUNCEMENTS
from services.runner import runner
from services.scanner import scan_all
from services.sync import sync_course, sync_workshop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    ccb_user = await runner.load_cached_account()
    if ccb_user:
        runner.current_ccb = ccb_user
    yield


app = FastAPI(title="CCB Course Bot", lifespan=lifespan)
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

STATIC_DIR = WEB_DIR / "static"


@app.get("/manifest.webmanifest", include_in_schema=False)
async def root_manifest():
    data = (STATIC_DIR / "manifest.webmanifest").read_bytes()
    return Response(content=data, media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def root_sw():
    data = (STATIC_DIR / "sw.js").read_bytes()
    return Response(
        content=data, media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.middleware("http")
async def no_html_cache(request: Request, call_next):
    resp: Response = await call_next(request)
    if "text/html" in resp.headers.get("content-type", ""):
        resp.headers["Cache-Control"] = NO_STORE
        resp.headers["Pragma"] = "no-cache"
    return resp


def _video_dict(v: Video) -> dict:
    pct = round(v.learned_seconds / v.duration * 100, 1) if v.duration else 0.0
    return {
        "id": v.id, "knowledge_id": v.knowledge_id, "title": v.title,
        "duration": round(v.duration, 1), "learned": round(v.learned_seconds, 1),
        "pct": min(100.0, pct), "status": v.status,
        "is_must": bool(v.is_must), "error": v.error,
        "running": runner.is_active(v.id),
    }


def _require_login() -> str:
    ccb = runner.current_ccb
    if not ccb:
        from services import login as login_svc
        raise login_svc.LoginError("未登录建行")
    return ccb


def extract_course_id(text: str) -> str:
    s = (text or "").strip()
    m = UUID_RE.search(s)
    return m.group(0) if m else s


# ==================== 建行登录 ====================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if runner.current_ccb and await runner.is_user_logged_in(runner.current_ccb):
        return HTMLResponse('<script>location.href="/";</script>')
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": ""}
    )


@app.post("/api/login")
async def api_login(username: str = Form(""), password: str = Form("")):
    try:
        data = await runner.login(username.strip(), password)
        runner.current_ccb = data.get("username") or username.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("login failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "username": runner.current_ccb}


@app.post("/api/logout")
async def api_logout():
    ccb = runner.current_ccb
    if ccb:
        await runner.logout(ccb)
    runner.current_ccb = ""
    return {"ok": True}


@app.get("/api/status")
async def api_status():
    ccb = runner.current_ccb
    logged = bool(ccb and await runner.is_user_logged_in(ccb))
    return {"logged_in": logged, "concurrency": runner.concurrency,
            "speed": runner.speed, "ccb_user": ccb or ""}


@app.post("/api/concurrency")
async def set_concurrency(value: int = Form(...)):
    if value != 0 and not (1 <= value <= 20):
        return JSONResponse(
            {"ok": False, "error": "并发数需为 0(无限制) 或 1-20"}, status_code=400
        )
    return {"ok": True, "concurrency": runner.set_concurrency(value)}


@app.post("/api/speed")
async def set_speed(value: float = Form(...)):
    try:
        speed = runner.set_speed(value)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "speed": speed}


# ==================== 专题班广场 ====================

@app.get("/workshops", response_class=HTMLResponse)
async def workshops_page(request: Request):
    if not (runner.current_ccb and
            await runner.is_user_logged_in(runner.current_ccb)):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": ""}
        )
    return templates.TemplateResponse(
        "workshops.html",
        {"request": request,
         "cn_name": await runner.get_cn_name(runner.current_ccb)}
    )


# 网络自学「链接学习页」（绕开签名广场，粘贴链接直接学）
LEARN_PAGES = {
    "wk": {"name": "微课", "hint": "粘贴微课链接，或微课 ID"},
    "course": {"name": "课程", "hint": "粘贴课程链接，或课程 ID"},
    "case": {"name": "案例", "hint": "粘贴案例链接，或案例 ID"},
}


@app.get("/learn/{kind}", response_class=HTMLResponse)
async def learn_page(kind: str, request: Request):
    if not (runner.current_ccb and
            await runner.is_user_logged_in(runner.current_ccb)):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": ""}
        )
    meta = LEARN_PAGES.get(kind)
    if meta is None:
        return JSONResponse({"error": "未知分类"}, status_code=404)
    return templates.TemplateResponse(
        "learn.html",
        {"request": request,
         "cn_name": await runner.get_cn_name(runner.current_ccb),
         "kind": kind,
         "page_name": meta["name"], "hint": meta["hint"]},
    )


# ==================== 网络自学封面广场 ====================
SQUARE_PAGES = {
    "wk": "微课广场",
    "course": "课程广场",
}


@app.get("/square/{kind}", response_class=HTMLResponse)
async def square_page(kind: str, request: Request):
    if not (runner.current_ccb and
            await runner.is_user_logged_in(runner.current_ccb)):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": ""}
        )
    page_name = SQUARE_PAGES.get(kind)
    if page_name is None:
        return JSONResponse({"error": "未知分类"}, status_code=404)
    return templates.TemplateResponse(
        "square.html",
        {"request": request,
         "cn_name": await runner.get_cn_name(runner.current_ccb),
         "kind": kind, "page_name": page_name},
    )


# ==================== 一键抓取（Bookmarklet 回跳自动学习） ====================

@app.get("/capture", response_class=HTMLResponse)
async def capture(request: Request, url: str = "", text: str = "", title: str = ""):
    if not (runner.current_ccb and
            await runner.is_user_logged_in(runner.current_ccb)):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": ""}
        )
    # 分享可能把链接放在 url 或 text（有的系统 text 含标题+URL）
    target = url.strip()
    if not target:
        t = text.strip()
        # 从 text 里抠 http 链接
        import re as _re
        m = _re.search(r'https?://\S+', t)
        target = m.group(0) if m else t
    return templates.TemplateResponse(
        "capture.html",
        {"request": request, "url": target,
         "cn_name": await runner.get_cn_name(runner.current_ccb)},
    )


async def _capture_one(client, ccb: str, raw: str, kind: str = "") -> dict:
    """单条链接学习：专题班走 learn_workshop，课程/微课同步入库并整课开刷。

    幂等：sync 为 upsert，重复提交同一课程不会产生重复记录。
    返回 {"kind": ..., "videos": n}。
    """
    from services import sync as sync_svc
    is_workshop = "workshop" in raw or "myworkshop" in raw
    is_case = "case" in raw
    if is_workshop:
        wid = extract_course_id(raw)
        await runner.learn_workshop(wid, ccb)
        return {"kind": "workshop", "videos": 0}
    ck = kind if kind in ("wk", "course") else ("case" if is_case else "course")
    pid, n = await sync_svc.sync_course(
        client, extract_course_id(raw), "", owner_ccb=ccb, kind=ck)
    async with SessionLocal() as session:
        course_pk = (await session.execute(
            select(Course.id).where(
                Course.package_id == pid, Course.owner_ccb == ccb)
        )).scalar_one_or_none()
    if course_pk is not None:
        await runner.start_course(course_pk, owner_ccb=ccb)
    return {"kind": ck, "videos": n}


@app.post("/api/capture")
async def api_capture(request: Request):
    ccb = _require_login()
    form = await request.form()
    raw = str(form.get("url") or "").strip()
    kind = str(form.get("kind") or "")
    client = await runner.client_for_user(ccb)
    try:
        try:
            result = await _capture_one(client, ccb, raw, kind)
        except Exception as exc:  # noqa: BLE001
            logger.exception("capture failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.aclose()
    return {"ok": True, **result}


@app.get("/api/workshops")
async def list_workshops(show: str = "unlearned"):
    """实时拉取专题班广场（不依赖扫描）。

    show=unlearned（默认）：只返回 progress<100 的未完成专题班；
    show=all：全部返回。
    """
    import asyncio as _aio
    ccb = _require_login()
    client = await runner.client_for_user(ccb)
    try:
        rows = await client.fetch_workshop_home(limit=100)
    finally:
        await client.aclose()
    raw_items = []
    for r in rows:
        raw_items.append({
            "id": str(r.get("id") or ""),
            "title": str(r.get("name") or r.get("title") or ""),
            "total_hours": r.get("totalHours"),
            "course_count": r.get("courseCount"),
            "progress": r.get("progress"),
            "praise": r.get("praiseCount") or r.get("praise"),
            "tag": (r.get("tagName") or r.get("tags") or ""),
            "cover": r.get("logoUrl") or "",
        })

    # 并发补个人真实进度并按未完成过滤
    client = await runner.client_for_user(ccb)
    sem = _aio.Semaphore(10)

    async def _prog(it) -> float | None:
        async with sem:
            return await client.fetch_workshop_progress(it["id"])

    try:
        progs = await _aio.gather(*[_prog(it) for it in raw_items])
    finally:
        await client.aclose()

    items = []
    ws_local = await _local_workshop_map(ccb)
    for it, p in zip(raw_items, progs):
        it["my_progress"] = p
        it["local_state"] = ws_local.get(str(it["id"]), "")
        if show == "all" or not (p is not None and p >= 100):
            items.append(it)
    return {"ok": True, "items": items, "show": show}


@app.post("/api/workshops/learn")
async def learn_workshop(workshop_id: str = Form(...)):
    ccb = _require_login()
    try:
        result = await runner.learn_workshop(workshop_id.strip(), ccb)
    except Exception as exc:  # noqa: BLE001
        logger.exception("learn workshop failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, **result}


async def _local_workshop_map(ccb: str) -> dict[str, str]:
    """返回本账号本地专题班 {workshopId(父Course.package_id): 状态}。

    专题班父记录不直挂视频，需递归所有子孙课程，看子孙视频：
    任一在播(running/queued 且任务存活)->playing；
    全部 done->done；其余（有未完成）->added。
    """
    from collections import defaultdict
    async with SessionLocal() as session:
        courses = list((await session.execute(
            select(Course).where(Course.owner_ccb == ccb)
        )).scalars())
        videos = list((await session.execute(
            select(Video).where(Video.owner_ccb == ccb)
        )).scalars())

    children: dict[int, list[int]] = defaultdict(list)
    by_id: dict[int, Course] = {}
    for c in courses:
        by_id[c.id] = c
        if c.parent_course_id is not None:
            children[c.parent_course_id].append(c.id)

    vids_by_course: dict[int, list] = defaultdict(list)
    for v in videos:
        vids_by_course[v.course_id].append(v)

    def descendants(root: int) -> list[int]:
        out: list[int] = []
        stack = list(children.get(root, []))
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(children.get(cur, []))
        return out

    result: dict[str, str] = {}
    for c in courses:
        if c.kind != "workshop" or c.parent_course_id is not None:
            continue  # 只处理顶层专题班
        all_ids = [c.id] + descendants(c.id)
        has_playing = False
        has_unfinished = False
        any_video = False
        for cid in all_ids:
            for v in vids_by_course.get(cid, []):
                any_video = True
                if v.status in ("running", "queued") and runner.is_active(v.id):
                    has_playing = True
                if v.status != "done":
                    has_unfinished = True
        if has_playing:
            st = "playing"
        elif any_video and not has_unfinished:
            st = "done"
        elif any_video:
            st = "added"
        else:
            st = "added"
        result[c.package_id] = st
    return result


async def _local_video_map(ccb: str) -> dict[str, str]:
    """返回本账号本地视频 {parent_id(广场课程UUID): 本地状态}。

    running/queued 且任务存活 -> playing（锁定）；
    done -> done；其余（pending/paused/failed）-> added（可继续，不锁）。
    """
    out: dict[str, str] = {}
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Video).where(Video.owner_ccb == ccb)
        )).scalars()
        for v in rows:
            if v.status in ("running", "queued") and runner.is_active(v.id):
                st = "playing"
            elif v.status == "done":
                st = "done"
            else:
                st = "added"
            if v.parent_id:
                # playing 优先（一个课程可能多条视频记录）
                if out.get(v.parent_id) != "playing":
                    out[v.parent_id] = st
    return out


async def _fetch_unlearned(client, module_type: int, need: int,
                           title: str = "") -> list[dict]:
    """从广场扫描「未完成」课程，最多取到 need 条。

    复用建行 centre/list 接口 + 逐条真实进度过滤（同 square unlearned 逻辑）。
    返回 _map_item 结构（含 hours 真实学时、knowledge_url）。
    """
    import asyncio as _aio
    module_map_inv = {1: "course", 4: "wk"}
    kind = module_map_inv.get(module_type, "course")
    MAX_SCAN = 300
    page = 20
    raw_off = 0
    out: list[dict] = []
    sem = _aio.Semaphore(10)
    while raw_off < MAX_SCAN and len(out) < need:
        url = ("https://api.u.ccb.com/v1/userSide/knowledge/centre/list"
               f"?offset={raw_off}&limit={page}")
        body = {"title": title, "moduleType": module_type, "orderType": 1,
                "displayEBookFlag": 0, "authTagIds": "[]", "theDeptFlag": 0,
                "lastMonth": 0, "orderTypeBy": 1}
        r = await client._client.post(url, json=body, timeout=30)
        if r.status_code != 200:
            break
        rows = r.json().get("datas") or r.json().get("data") or []
        if not rows:
            break

        async def _check(x):
            async with sem:
                p = await client.fetch_knowledge_progress(str(x.get("id")))
            return x, p

        checked = await _aio.gather(*[_check(x) for x in rows])
        for x, p in checked:
            if not (p is not None and p >= 100):
                it = {
                    "id": x.get("id"), "title": x.get("title"),
                    "hours": x.get("knowledgeHours"),
                    "knowledge_url": x.get("knowledgeUrl"),
                    "kind": kind,
                }
                out.append(it)
                if len(out) >= need:
                    break
        raw_off += len(rows)
        if len(rows) < page:
            break
    return out


ONLINE_TARGET_HOURS = 51.0


async def _fetch_unlearned_wks_shortest(client, gap_hours: float,
                                        title: str = "") -> list[dict]:
    """扫描未学微课，按学时「从小到大」选取，累计学时覆盖 gap_hours。

    优先刷最短的课以最快刷满。收集后排序、从最短开始累加。
    """
    import asyncio as _aio
    module_type = 4
    kind = "wk"
    MAX_SCAN = 400
    page = 20
    raw_off = 0
    pool: list[dict] = []
    sem = _aio.Semaphore(10)
    while raw_off < MAX_SCAN:
        url = ("https://api.u.ccb.com/v1/userSide/knowledge/centre/list"
               f"?offset={raw_off}&limit={page}")
        body = {"title": title, "moduleType": module_type, "orderType": 1,
                "displayEBookFlag": 0, "authTagIds": "[]", "theDeptFlag": 0,
                "lastMonth": 0, "orderTypeBy": 1}
        r = await client._client.post(url, json=body, timeout=30)
        if r.status_code != 200:
            break
        rows = r.json().get("datas") or r.json().get("data") or []
        if not rows:
            break

        async def _check(x):
            async with sem:
                p = await client.fetch_knowledge_progress(str(x.get("id")))
            return x, p

        checked = await _aio.gather(*[_check(x) for x in rows])
        for x, p in checked:
            if not (p is not None and p >= 100):
                pool.append({
                    "id": x.get("id"), "title": x.get("title"),
                    "hours": float(x.get("knowledgeHours") or 0),
                    "knowledge_url": x.get("knowledgeUrl"),
                    "kind": kind,
                })
        raw_off += len(rows)
        if len(rows) < page:
            break

    # 学时升序：最短的先刷；加 0.5 学时冗余保证够
    pool.sort(key=lambda it: it["hours"])
    out: list[dict] = []
    acc = 0.0
    for it in pool:
        out.append(it)
        acc += it["hours"]
        if acc >= gap_hours + 0.5:
            break
    return out


async def _capture_list(client, ccb: str, selected: list[dict], conc: int = 5):
    """并发 capture 一批课程，返回统计 dict。"""
    import asyncio as _aio
    cap_sem = _aio.Semaphore(conc)
    results = {"ok": 0, "fail": 0, "errors": []}
    n_course = n_wk = 0
    got_hours = 0.0

    async def _run(it):
        nonlocal n_course, n_wk, got_hours
        url = "https://u.ccb.com" + str(it.get("knowledge_url") or "")
        async with cap_sem:
            try:
                await _capture_one(client, ccb, url, it["kind"])
                results["ok"] += 1
                got_hours += float(it.get("hours") or 0)
                if it["kind"] == "wk":
                    n_wk += 1
                else:
                    n_course += 1
            except Exception as exc:  # noqa: BLE001
                results["fail"] += 1
                if len(results["errors"]) < 5:
                    results["errors"].append(str(exc)[:120])

    await _aio.gather(*[_run(it) for it in selected])
    results.update(n_course=n_course, n_wk=n_wk, got_hours=round(got_hours, 2))
    return results


@app.post("/api/auto-fill-online")
async def auto_fill_online():
    """一键补齐网络自学学时（固定目标51学时）。

    选课：先取课程广场前30节未学课按真实学时累加；不足部分用微课补齐。
    选中课程逐条 capture 入库并自动开刷，单条失败不阻塞。
    """
    import asyncio as _aio
    ccb = _require_login()
    client = await runner.client_for_user(ccb)
    try:
        try:
            h = await client.fetch_study_hours_home()
            done_hours = float(h.get("yearOnlDrtn") or 0)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "error": f"读取当前学时失败：{exc}"}, status_code=400)

        gap = round(ONLINE_TARGET_HOURS - done_hours, 2)
        if gap <= 0:
            return {"ok": True, "already_done": True, "done_hours": done_hours,
                    "message": f"网络自学已完成 {done_hours} 学时，无需补齐"}

        # 1) 课程前30节未学
        courses = await _fetch_unlearned(client, 1, 30)
        selected: list[dict] = []
        acc = 0.0
        for it in courses:
            selected.append(it)
            acc += float(it.get("hours") or 0)
            if acc >= gap:
                break
        # 2) 不足则用微课补（上限200节兜底）
        if acc < gap:
            wks = await _fetch_unlearned(client, 4, 200)
            for it in wks:
                selected.append(it)
                acc += float(it.get("hours") or 0)
                if acc >= gap:
                    break

        if not selected:
            return JSONResponse(
                {"ok": False, "error": "广场未找到可学的未完成课程"}, status_code=400)

        # 逐条 capture，并发5（加速但不过度请求）
        cap_sem = _aio.Semaphore(5)
        results = {"ok": 0, "fail": 0, "errors": []}
        n_course = n_wk = 0
        got_hours = 0.0

        async def _run(it):
            nonlocal n_course, n_wk, got_hours
            url = "https://u.ccb.com" + str(it.get("knowledge_url") or "")
            async with cap_sem:
                try:
                    await _capture_one(client, ccb, url, it["kind"])
                    results["ok"] += 1
                    got_hours += float(it.get("hours") or 0)
                    if it["kind"] == "wk":
                        n_wk += 1
                    else:
                        n_course += 1
                except Exception as exc:  # noqa: BLE001
                    results["fail"] += 1
                    if len(results["errors"]) < 5:
                        results["errors"].append(str(exc)[:120])

        await _aio.gather(*[_run(it) for it in selected])

        return {"ok": True, "already_done": False, "done_hours": done_hours,
                "target": ONLINE_TARGET_HOURS, "gap": gap,
                "selected": len(selected), "started": results["ok"],
                "failed": results["fail"], "errors": results["errors"],
                "n_course": n_course, "n_wk": n_wk,
                "got_hours": round(got_hours, 2)}
    finally:
        await client.aclose()


@app.post("/api/auto-fill-online-fast")
async def auto_fill_online_fast():
    """极速补齐网络自学学时：只刷微课、按学时从小到大选，全并发刷满。"""
    import asyncio as _aio
    ccb = _require_login()
    client = await runner.client_for_user(ccb)
    try:
        try:
            h = await client.fetch_study_hours_home()
            done_hours = float(h.get("yearOnlDrtn") or 0)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "error": f"读取当前学时失败：{exc}"}, status_code=400)

        gap = round(ONLINE_TARGET_HOURS - done_hours, 2)
        if gap <= 0:
            return {"ok": True, "already_done": True, "done_hours": done_hours,
                    "message": f"网络自学已完成 {done_hours} 学时，无需补齐"}

        selected = await _fetch_unlearned_wks_shortest(client, gap)

        if not selected:
            return JSONResponse(
                {"ok": False,
                 "error": "微课广场未找到可学的未完成微课"}, status_code=400)

        # 全并发 capture（微课短小，极速刷满）
        stats = await _capture_list(client, ccb, selected, conc=10)

        return {"ok": True, "already_done": False, "fast": True,
                "done_hours": done_hours,
                "target": ONLINE_TARGET_HOURS, "gap": gap,
                "selected": len(selected), "started": stats["ok"],
                "failed": stats["fail"], "errors": stats["errors"],
                "n_course": stats["n_course"], "n_wk": stats["n_wk"],
                "got_hours": stats["got_hours"]}
    finally:
        await client.aclose()


# ==================== 添加课程 ====================

# ==================== 网络自学广场（课程/微课/案例） ====================

@app.get("/api/square/{kind}")
async def square_list(kind: str, offset: int = 0, limit: int = 20,
                       title: str = "", order_type: int = 1,
                       show: str = "unlearned"):
    """课程/微课列表，无需签名。kind: course=mt1, wk=mt4。

    show=unlearned（默认）：只返回建行真实进度<100 的课；后端内部翻页
    补齐到 limit 条未学内容。show=all：原样返回。
    注意：这里 offset 是"未学序列"的偏移，不是建行原始 offset。
    """
    import asyncio as _aio
    module_map = {"course": 1, "wk": 4}
    mt = module_map.get(kind)
    if mt is None:
        return JSONResponse({"ok": False, "error": "未知分类"}, status_code=404)
    ccb = _require_login()
    client = await runner.client_for_user(ccb)
    sem = _aio.Semaphore(10)

    def _map_item(x) -> dict:
        return {
            "id": x.get("id"),
            "title": x.get("title"),
            "cover": x.get("photoUrl"),
            "author": x.get("author"),
            "dept": x.get("ouName"),
            "hours": x.get("knowledgeHours"),
            "std_hours": x.get("standardStudyHours"),
            "score": x.get("averageCommentScore"),
            "persons": x.get("studyPersonCount"),
            "supports": x.get("supportCount"),
            "file_type": x.get("fileType"),
            "knowledge_url": x.get("knowledgeUrl"),
        }

    try:
        # show=all：直接取一页
        if show == "all":
            url = ("https://api.u.ccb.com/v1/userSide/knowledge/centre/list"
                   f"?offset={offset}&limit={limit}")
            body = {"title": title, "moduleType": mt, "orderType": order_type,
                    "displayEBookFlag": 0, "authTagIds": "[]", "theDeptFlag": 0,
                    "lastMonth": 0, "orderTypeBy": 1}
            r = await client._client.post(url, json=body, timeout=30)
            if r.status_code != 200:
                return JSONResponse(
                    {"ok": False, "error": r.text[:300]}, status_code=r.status_code)
            rows = r.json().get("datas") or r.json().get("data") or []
            items = [_map_item(x) for x in rows]
            local_map = await _local_video_map(ccb)
            for it in items:
                it["local_state"] = local_map.get(str(it["id"]), "")
            return {"ok": True, "items": items, "offset": offset,
                    "limit": limit, "show": show, "has_more": len(rows) >= limit}

        # unlearned：从建行原始 0 开始扫描，跳过已完成，按未学偏移切片。
        # 为控制成本，最多扫描原始 300 条。
        MAX_SCAN = 300
        page = max(limit, 20)
        unlearned: list[dict] = []
        raw_off = 0
        reached_end = False
        while raw_off < MAX_SCAN:
            url = ("https://api.u.ccb.com/v1/userSide/knowledge/centre/list"
                   f"?offset={raw_off}&limit={page}")
            body = {"title": title, "moduleType": mt, "orderType": order_type,
                    "displayEBookFlag": 0, "authTagIds": "[]", "theDeptFlag": 0,
                    "lastMonth": 0, "orderTypeBy": 1}
            r = await client._client.post(url, json=body, timeout=30)
            if r.status_code != 200:
                return JSONResponse(
                    {"ok": False, "error": r.text[:300]}, status_code=r.status_code)
            rows = r.json().get("datas") or r.json().get("data") or []
            if len(rows) < page:
                reached_end = True

            async def _check(x):
                async with sem:
                    p = await client.fetch_knowledge_progress(str(x.get("id")))
                return x, p

            checked = await _aio.gather(*[_check(x) for x in rows])
            for x, p in checked:
                if not (p is not None and p >= 100):
                    it = _map_item(x)
                    it["my_progress"] = p
                    unlearned.append(it)

            raw_off += len(rows)
            if reached_end:
                break
            # 已能覆盖本次请求窗口且确认还有剩余，即可停止
            if len(unlearned) >= offset + limit and not reached_end:
                # 多取一页以判断 has_more 是否准确：继续直到能确定
                # 简化：只要未到结尾就认为还有更多
                break

        page_items = unlearned[offset:offset + limit]
        local_map = await _local_video_map(ccb)
        for it in page_items:
            it["local_state"] = local_map.get(str(it["id"]), "")
        # has_more：未学序列在窗口后还有；或原始未扫完（可能还有未学）
        has_more = len(unlearned) > offset + limit or (
            raw_off < MAX_SCAN and not reached_end)
        return {"ok": True, "items": page_items, "offset": offset,
                "limit": limit, "show": show, "has_more": has_more}
    finally:
        await client.aclose()


@app.post("/api/courses")
async def add_course(package_id: str = Form(...), title: str = Form("")):
    raw = package_id.strip()
    pid = extract_course_id(raw)
    is_workshop = "workshop" in raw or "myworkshop" in raw
    is_case = "case" in raw
    try:
        ccb = _require_login()
        client = await runner.client_for_user(ccb)
        if is_workshop:
            _, n = await sync_workshop(client, pid, title.strip(), owner_ccb=ccb)
        else:
            ck = "case" if is_case else "course"
            _, n = await sync_course(client, pid, title.strip(),
                                     owner_ccb=ccb, kind=ck)
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.exception("sync failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    kind = "workshop" if is_workshop else ("case" if is_case else "course")
    return {"ok": True, "videos": n, "kind": kind}


# ==================== 扫描 ====================

@app.post("/api/scan")
async def api_scan():
    expanded, failed = [], []
    try:
        ccb = _require_login()
        client = await runner.client_for_user(ccb)
        stats = await scan_all(client)
        async with SessionLocal() as session:
            todo = [(r.ref_id, r.title) for r in (await session.execute(
                select(StudyItem).where(
                    StudyItem.item_type == "workshop",
                    StudyItem.action == "ready",
                )
            )).scalars()]
        for ref_id, t in todo:
            try:
                await sync_workshop(client, ref_id, t, owner_ccb=ccb)
                expanded.append(t)
            except Exception as exc:  # noqa: BLE001
                logger.warning("展开失败 %s: %s", t, exc)
                failed.append({"title": t, "error": str(exc)})
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "stats": stats,
            "expanded": expanded, "failed": failed}


# ==================== 首页三态 ====================

def _course_bucket(total: int, done: int, has_active: bool) -> str:
    if has_active:
        return "watching"
    if total > 0 and done >= total:
        return "done"
    return "ready"


def build_overview(courses, videos, is_active) -> dict:
    """一次性汇总，返回 course_id -> {total,done,active}。

    普通课程：直接数自身视频。
    workshop 父班：递归汇总所有子孙课程的视频（修复首页 0/0）。
    courses/videos 为已分离的 ORM 对象列表。
    """
    # 每个非父课程：直接统计自身视频
    vids_by_course: dict[int, list] = {}
    for v in videos:
        vids_by_course.setdefault(v.course_id, []).append(v)

    agg = {c.id: {"total": 0, "done": 0, "active": False} for c in courses}

    def course_is_workshop(c):
        return c.kind == "workshop"

    # 先放每个课程“自身直挂视频”，仅对非workshop叶子计入自身；
    # workshop 的数字来自其子课程。
    for c in courses:
        rec = agg[c.id]
        if not course_is_workshop(c):
            vs = vids_by_course.get(c.id, [])
            rec["total"] = len(vs)
            rec["done"] = sum(1 for v in vs if v.status == "done")
            rec["active"] = any(
                v.status in ("running", "queued") and is_active(v.id) for v in vs
            )

    # 自底向上：每个 workshop = 直接子课程聚合之和
    # 用重复松弛（最大20层）
    for _ in range(20):
        changed = False
        for c in courses:
            if not course_is_workshop(c):
                continue
            t = d = a = 0
            for ch in courses:
                if ch.parent_course_id == c.id:
                    r = agg[ch.id]
                    t += r["total"]; d += r["done"]
                    a = a or r["active"]
            r = agg[c.id]
            if (r["total"], r["done"], r["active"]) != (t, d, a):
                r["total"], r["done"], r["active"] = t, d, a
                changed = True
        if not changed:
            break
    return agg


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not (runner.current_ccb and
            await runner.is_user_logged_in(runner.current_ccb)):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": ""}
        )
    ccb = runner.current_ccb
    buckets = {"ready": [], "watching": [], "done": []}
    async with SessionLocal() as session:
        course_rows = (await session.execute(
            select(Course).where(Course.owner_ccb == ccb).order_by(Course.id.desc())
        )).scalars()
        courses = list(course_rows)
        video_rows = (await session.execute(
            select(Video).where(Video.owner_ccb == ccb)
        )).scalars()
        videos = list(video_rows)
        session.expunge_all()

    overview = build_overview(courses, videos, runner.is_active)

    # 首页只显示顶层条目：父班（workshop）+ 无父课程；
    # 有父的子课程不重复平铺（进父班详情查看）。
    for c in courses:
        if c.parent_course_id is not None:
            continue
        rec = overview[c.id]
        total, done, has_active = rec["total"], rec["done"], rec["active"]
        bucket = _course_bucket(
            total, done, has_active
        )
        buckets[bucket].append({
            "id": c.id, "title": c.title, "total": total, "done": done,
            "kind": c.kind,
            "pct": round(done / total * 100, 1) if total else 0.0,
            "active": has_active,
        })

    hours_cards = []
    cn_name = ccb
    try:
        client = await runner.client_for_user(ccb)
        h = await client.fetch_study_hours_home()
        await client.aclose()
        cn_name = str(h.get("cnName") or ccb)

        def _card(name, dk, tk):
            done = float(h.get(dk) or 0)
            target = float(h.get(tk) or 0)
            pct = round(done / target * 100, 1) if target else 0.0
            return {"name": name, "done": round(done, 2),
                    "target": round(target, 2),
                    "gap": round(max(0.0, target - done), 2),
                    "pct": min(100.0, pct)}

        hours_cards = [
            _card("今年集中培训", "yearTrDrtn", "foucusUpToHours"),
            _card("今年网络自学", "yearOnlDrtn", "selfStudyUpToHours"),
        ]
    except Exception:  # noqa: BLE001
        logger.warning("学时读取失败", exc_info=True)

    if buckets["watching"]:
        default_tab = "watching"
    elif buckets["ready"]:
        default_tab = "ready"
    else:
        default_tab = "done"

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "buckets": buckets, "default_tab": default_tab,
         "cn_name": cn_name,
         "concurrency": runner.concurrency, "hours_cards": hours_cards},
    )


# ==================== 详情 ====================

@app.get("/courses/{course_pk}")
async def course_detail_page(request: Request, course_pk: int):
    ccb = _require_login()
    async with SessionLocal() as session:
        course = await session.get(Course, course_pk)
        if course is None or course.owner_ccb != ccb:
            return JSONResponse({"error": "无权访问或不存在"}, status_code=403)

        if course.kind == "workshop":
            children = (await session.execute(
                select(Course).where(
                    Course.parent_course_id == course_pk
                ).order_by(Course.id)
            )).scalars()
            child_list = []
            for ch in children:
                total = ch.total_videos
                done = (await session.execute(
                    select(func.count(Video.id)).where(
                        Video.course_id == ch.id, Video.status == "done"
                    )
                )).scalar() or 0
                child_list.append({
                    "id": ch.id, "title": ch.title, "total": total,
                    "done": done,
                    "pct": round(done / total * 100, 1) if total else 0.0,
                })
            meta = json.loads(course.workshop_meta or "{}")
            st = sum(c["total"] for c in child_list)
            sd = sum(c["done"] for c in child_list)
            meta["progress"] = round(sd / st * 100, 2) if st else 0.0
            return templates.TemplateResponse(
                "workshop.html",
                {"request": request, "course": course, "meta": meta,
                 "children_json": json.dumps(child_list, ensure_ascii=False)},
            )

        videos = (await session.execute(
            select(Video).where(Video.course_id == course_pk).order_by(Video.id)
        )).scalars()
        data = [_video_dict(v) for v in videos]
        # 播放页标签按真实来源 kind：专题班绿 / 微课蓝 / 课程橘
        if course.kind == "workshop":
            kind_label, kind_cls = "专题班", "ws"
        elif course.kind == "wk":
            kind_label, kind_cls = "微课", "wk"
        else:
            kind_label, kind_cls = "课程", "course"
    return templates.TemplateResponse(
        "course.html",
        {"request": request, "course": course,
         "kind_label": kind_label, "kind_cls": kind_cls,
         "videos_json": json.dumps(data, ensure_ascii=False)},
    )


@app.get("/api/my-courses")
async def my_courses():
    """当前建行账号顶层课程实时汇总（供首页静默刷新）。"""
    ccb = _require_login()
    async with SessionLocal() as session:
        courses = list((await session.execute(
            select(Course).where(Course.owner_ccb == ccb).order_by(Course.id.desc())
        )).scalars())
        videos = list((await session.execute(
            select(Video).where(Video.owner_ccb == ccb)
        )).scalars())
        session.expunge_all()
    overview = build_overview(courses, videos, runner.is_active)
    out = []
    for c in courses:
        if c.parent_course_id is not None:
            continue
        rec = overview[c.id]
        total, done, has_active = rec["total"], rec["done"], rec["active"]
        out.append({
            "id": c.id, "total": total, "done": done, "kind": c.kind,
            "pct": round(done / total * 100, 1) if total else 0.0,
            "active": has_active,
        })
    return {"courses": out}


@app.get("/api/courses/{course_pk}/videos")
async def list_videos(course_pk: int):
    ccb = _require_login()
    async with SessionLocal() as session:
        course = await session.get(Course, course_pk)
        if course is None or course.owner_ccb != ccb:
            return JSONResponse({"error": "无权访问"}, status_code=403)
        videos = (await session.execute(
            select(Video).where(Video.course_id == course_pk).order_by(Video.id)
        )).scalars()
        return {"videos": [_video_dict(v) for v in videos]}


# ==================== 刷课控制 ====================

@app.post("/api/courses/{course_pk}/start")
async def start_course_api(course_pk: int):
    ccb = _require_login()
    async with SessionLocal() as session:
        course = await session.get(Course, course_pk)
        if course is None or course.owner_ccb != ccb:
            return JSONResponse({"ok": False, "error": "无权访问"}, status_code=403)
        kind = course.kind
    if kind == "workshop":
        await runner.start_workshop(course_pk, owner_ccb=ccb)
    else:
        await runner.start_course(course_pk, owner_ccb=ccb)
    return {"ok": True}


@app.post("/api/videos/{video_pk}/start")
async def start_video_api(video_pk: int):
    ccb = _require_login()
    async with SessionLocal() as session:
        video = await session.get(Video, video_pk)
        if video is None or video.owner_ccb != ccb:
            return JSONResponse({"ok": False, "error": "无权访问"}, status_code=403)
    await runner.start_video(video_pk, owner_ccb=ccb)
    return {"ok": True}


@app.post("/api/videos/{video_pk}/pause")
async def pause_video_api(video_pk: int):
    ccb = _require_login()
    async with SessionLocal() as session:
        video = await session.get(Video, video_pk)
        if video is None or video.owner_ccb != ccb:
            return JSONResponse({"ok": False, "error": "无权访问"}, status_code=403)
    await runner.pause_video(video_pk)
    return {"ok": True}


# ==================== 清除记录 ====================

@app.post("/api/courses/clear")
async def clear_bucket(bucket: str = Form(...)):
    """清除本建行账号指定分区的本地课程记录。

    bucket: ready=准备观看 done=已观看 watching=正在观看（先停止）
    仅删本地数据，不影响建行平台。
    """
    if bucket not in ("ready", "done", "watching"):
        return JSONResponse({"ok": False, "error": "非法分区"}, status_code=400)
    ccb = _require_login()
    async with SessionLocal() as session:
        courses = list((await session.execute(
            select(Course).where(Course.owner_ccb == ccb)
        )).scalars())
        videos = list((await session.execute(
            select(Video).where(Video.owner_ccb == ccb)
        )).scalars())
    overview = build_overview(courses, videos, runner.is_active)

    # 仅顶层条目参与判定与删除
    targets = []
    for c in courses:
        if c.parent_course_id is not None:
            continue
        rec = overview[c.id]
        total, done, has_active = rec["total"], rec["done"], rec["active"]
        b = "watching" if has_active else (
            "done" if (total > 0 and done >= total) else "ready"
        )
        if b == bucket:
            targets.append(c)

    n = 0
    async with SessionLocal() as session:
        for c in targets:
            # 收集该顶层条目下所有视频（父班含子课程视频）先停止
            vids = list((await session.execute(
                select(Video.id).where(Video.owner_ccb == ccb)
            )).scalars())
            # 更精确：仅该顶层及其子孙的视频；全部停止影响小
            for vid in vids:
                await runner.pause_video(vid)
            top = await session.get(Course, c.id)
            await session.delete(top)  # 级联删除子课程/视频
            n += 1
        await session.commit()
    return {"ok": True, "deleted": n}


APP_VERSION = "v3.36"


@app.get("/api/version")
async def version_info():
    return {"version": APP_VERSION, "name": "建行学习刷课助手"}


@app.get("/api/announcement")
async def get_announcement():
    """返回当前版本公告及是否需要弹窗。

    需要弹：无记录、seen!=1，或记录的版本与当前版本不一致（新版本自动重置）。
    """
    info = ANNOUNCEMENTS.get(APP_VERSION, {"date": "", "items": []})
    async with SessionLocal() as session:
        row = (await session.execute(select(Announcement).limit(1))).scalar_one_or_none()
        should_show = row is None or row.seen != 1 or row.seen_version != APP_VERSION
    return {
        "version": APP_VERSION,
        "date": info.get("date", ""),
        "items": info.get("items", []),
        "show": should_show,
    }


@app.post("/api/announcement/read")
async def mark_announcement_read():
    """关闭弹窗：置 seen=1，并同步当前版本号。"""
    async with SessionLocal() as session:
        row = (await session.execute(select(Announcement).limit(1))).scalar_one_or_none()
        if row is None:
            row = Announcement()
            session.add(row)
        row.seen = 1
        row.seen_version = APP_VERSION
        await session.commit()
    return {"ok": True}
