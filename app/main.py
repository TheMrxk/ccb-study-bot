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
from models import Course, StudyItem, Video
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


@app.post("/api/capture")
async def api_capture(request: Request):
    from services import sync as sync_svc
    ccb = _require_login()
    form = await request.form()
    url = str(form.get("url") or "")
    kind = str(form.get("kind") or "")
    raw = url.strip()
    is_workshop = "workshop" in raw or "myworkshop" in raw
    is_case = "case" in raw
    client = await runner.client_for_user(ccb)
    try:
        if is_workshop:
            wid = extract_course_id(raw)
            result = await runner.learn_workshop(wid, ccb)
            kind = "workshop"
        else:
            ck = kind if kind in ("wk", "course") else (
                "case" if is_case else "course")
            pid, n = await sync_svc.sync_course(
                client, extract_course_id(raw), "",
                owner_ccb=ccb, kind=ck)
            # 找到本地课程并启动整个课程
            async with SessionLocal() as session:
                course_pk = (await session.execute(
                    select(Course.id).where(
                        Course.package_id == pid,
                        Course.owner_ccb == ccb,
                    )
                )).scalar_one_or_none()
            if course_pk is not None:
                await runner.start_course(course_pk, owner_ccb=ccb)
            result, kind = {"videos": n}, ck
    except Exception as exc:  # noqa: BLE001
        logger.exception("capture failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.aclose()
    return {"ok": True, "kind": kind, **result}


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
    for it, p in zip(raw_items, progs):
        it["my_progress"] = p
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


@app.get("/api/version")
async def version_info():
    return {"version": "v3.22", "name": "建行学习刷课助手"}
