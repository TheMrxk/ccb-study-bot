"""调度器：token 生命周期、并发控制、批次级开始/停止、单个重试。"""
from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from datetime import datetime

from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import Account, Course, TaskLog, Video
from services import login as login_svc
from services.ccb_api import ApiError, CcbClient
from services.progress import run_video

logger = logging.getLogger(__name__)
settings = get_settings()

# 并发上限：0 或负数视为"无限制"。内部用一个足够大的值实现。
UNLIMITED = 10_000


def _resolve_concurrency(value: int) -> int:
    if value is None or value <= 0:
        return UNLIMITED
    return max(1, value)


class Runner:
    def __init__(self) -> None:
        self._token_lock = asyncio.Lock()
        # 当前登录的建行用户名（唯一身份）
        self.current_ccb: str = ""

        self._concurrency = _resolve_concurrency(settings.concurrency)
        self._sem = asyncio.Semaphore(self._concurrency)
        # 刷课倍速：上报推进/真实等待 之比。默认2倍
        self._speed: float = 2.0

        # video.id -> stop event
        self._controls: dict[int, asyncio.Event] = {}
        # video.id -> asyncio.Task
        self._tasks: dict[int, asyncio.Event] = {}
        # video.id -> batch id（单个启动则为 None）
        self._video_batch: dict[int, str | None] = {}
        # batch id -> set(video.id)
        self._batch_videos: dict[str, set[int]] = {}
        # video.id -> owner_user_id（worker 取票用）
        self._video_owner: dict[int, str] = {}

    # ---------- 并发设置（运行时可调） ----------
    @property
    def concurrency(self) -> int:
        return 0 if self._concurrency >= UNLIMITED else self._concurrency

    def set_concurrency(self, value: int) -> int:
        """调整并发上限。0=无限制。需重建信号量；已在运行的任务不受影响。"""
        self._concurrency = _resolve_concurrency(value)
        self._sem = asyncio.Semaphore(self._concurrency)
        return self.concurrency

    # ---------- 倍速设置（运行时可调） ----------
    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, value: float) -> float:
        v = float(value)
        if v < 1.0 or v > 2.0:
            raise ValueError("倍速需在 1-2 之间")
        self._speed = v
        return self._speed

    # ---------- token（按建行用户名） ----------
    async def login(self, username: str, password: str) -> dict:
        """页面自主登录：凭证只用于本次换取票据，不保存账号密码。

        建行用户名（username）即唯一身份。
        """
        async with self._token_lock:
            data = await login_svc.fetch_token(username, password)
            await self._persist_account(data)
            return data

    async def is_user_logged_in(self, ccb_user: str) -> bool:
        return bool(await self.get_user_token(ccb_user))

    async def get_user_token(self, ccb_user: str) -> str:
        async with SessionLocal() as session:
            acc = (await session.execute(
                select(Account).where(Account.username == ccb_user).limit(1)
            )).scalar_one_or_none()
            return acc.token if acc else ""

    async def get_user_org_id(self, ccb_user: str) -> str:
        async with SessionLocal() as session:
            acc = (await session.execute(
                select(Account).where(Account.username == ccb_user).limit(1)
            )).scalar_one_or_none()
            return acc.org_id if acc else ""

    async def ensure_user_token(self, ccb_user: str) -> str:
        token = await self.get_user_token(ccb_user)
        if not token:
            raise login_svc.LoginError("未登录或会话已过期，请重新登录建行")
        return token

    async def client_for_user(self, ccb_user: str) -> CcbClient:
        token = await self.ensure_user_token(ccb_user)
        org_id = await self.get_user_org_id(ccb_user)
        return CcbClient(token, org_id)

    async def get_cn_name(self, ccb_user: str) -> str:
        """统一解析中文姓名并按账号缓存；失败回退账号名。

        所有页面侧边栏都用它，避免二级页直接显示账号(visitor)。
        """
        cache = getattr(self, "_cn_name_cache", None)
        if cache is None:
            cache = self._cn_name_cache = {}
        if ccb_user in cache:
            return cache[ccb_user]
        name = ccb_user
        client = await self.client_for_user(ccb_user)
        try:
            h = await client.fetch_study_hours_home()
            name = str(h.get("cnName") or ccb_user)
        except Exception:  # noqa: BLE001
            name = ccb_user
        finally:
            await client.aclose()
        cache[ccb_user] = name
        return name

    async def logout(self, ccb_user: str) -> None:
        async with SessionLocal() as session:
            await session.execute(
                Account.__table__.update()
                .where(Account.username == ccb_user)
                .values(token="", status="inactive")
            )
            await session.commit()
        cache = getattr(self, "_cn_name_cache", None)
        if cache:
            cache.pop(ccb_user, None)

    async def _persist_account(self, data: dict) -> None:
        uname = data.get("username") or "ccb_user"
        async with SessionLocal() as session:
            acc = (await session.execute(
                select(Account).where(Account.username == uname)
            )).scalar_one_or_none()
            now = datetime.utcnow()
            if acc is None:
                acc = Account(username=uname)
                session.add(acc)
            acc.token = data["token"]
            acc.user_id = data.get("user_id", "")
            acc.org_id = data.get("org_id", "")
            acc.status = "active"
            acc.token_updated_at = now
            await session.commit()

    async def load_cached_account(self) -> str:
        """启动时恢复最近账户用户名（其 token 仍在库），返回 ccb_user。"""
        async with SessionLocal() as session:
            acc = (await session.execute(
                select(Account).order_by(Account.token_updated_at.desc()).limit(1)
            )).scalar_one_or_none()
            return acc.username if (acc and acc.token) else ""

    # ---------- 状态查询 ----------
    def is_active(self, video_pk: int) -> bool:
        return video_pk in self._controls

    def active_overview(self) -> dict:
        return {
            "active": len(self._controls),
            "concurrency": self.concurrency,
            "speed": self._speed,
            "batches": list(self._batch_videos.keys()),
        }

    # ---------- 单个视频（不归批次） ----------
    async def start_video(self, video_pk: int, batch_id: str | None = None,
                          owner_ccb: str | None = None) -> None:
        existing = self._tasks.get(video_pk)
        if existing and not existing.done():
            if batch_id is not None:
                self._video_batch[video_pk] = batch_id
                self._batch_videos.setdefault(batch_id, set()).add(video_pk)
            if owner_ccb is not None:
                self._video_owner[video_pk] = owner_ccb
            return

        stop = asyncio.Event()
        self._controls[video_pk] = stop
        self._video_batch[video_pk] = batch_id
        if owner_ccb is not None:
            self._video_owner[video_pk] = owner_ccb
        if batch_id is not None:
            self._batch_videos.setdefault(batch_id, set()).add(video_pk)

        task = asyncio.create_task(self._guarded_worker(video_pk, stop))
        self._tasks[video_pk] = task

    async def pause_video(self, video_pk: int) -> None:
        stop = self._controls.get(video_pk)
        if stop:
            stop.set()

    # ---------- 课程 / 专题班（批次） ----------
    async def start_course(self, course_pk: int, batch_id: str | None = None,
                           owner_ccb: str | None = None) -> str:
        """启动一个子课程的全部未完成视频，归入同一批次，返回 batch_id。"""
        if batch_id is None:
            batch_id = uuid_lib.uuid4().hex
        async with SessionLocal() as session:
            result = await session.execute(
                select(Video.id).where(
                    Video.course_id == course_pk,
                    Video.status.in_(["pending", "queued", "failed", "paused"]),
                )
            )
            ids = [v for v in result.scalars()]
        for vid in ids:
            await self.start_video(vid, batch_id=batch_id, owner_ccb=owner_ccb)
        return batch_id

    async def start_workshop(self, workshop_pk: int,
                             owner_ccb: str | None = None) -> str:
        """整个专题班开始：所有子课程的未完成视频归入一个批次。"""
        batch_id = uuid_lib.uuid4().hex
        async with SessionLocal() as session:
            child_ids = list((await session.execute(
                select(Course.id).where(Course.parent_course_id == workshop_pk)
            )).scalars())
        for cid in child_ids:
            await self.start_course(
                cid, batch_id=batch_id, owner_ccb=owner_ccb
            )
        return batch_id

    async def learn_workshop(self, workshop_id: str, owner_ccb: str) -> dict:
        """一键学习专题班：报名 → 同步入库 → 整个班开始刷。

        报名容错：已报名 / 重复报名的报错不致命，继续同步+刷课。
        返回 {course_pk, batch_id, registered}。
        """
        from services import sync as sync_svc

        client: CcbClient = await self.client_for_user(owner_ccb)
        registered = False
        try:
            await client.register_workshop(workshop_id)
            registered = True
        except ApiError as exc:
            # 已报名通常返回业务错误码而非401；401则上抛
            if exc.status_code == 401:
                raise
            logger.info("报名跳过（可能已报名）：%s", exc)
        # 同步入库
        await sync_svc.sync_workshop(client, workshop_id, "", owner_ccb=owner_ccb)
        await client.aclose()

        # 找到本地父记录主键
        async with SessionLocal() as session:
            parent_pk = (await session.execute(
                select(Course.id).where(
                    Course.package_id == workshop_id,
                    Course.owner_ccb == owner_ccb,
                )
            )).scalar_one()
        batch_id = await self.start_workshop(parent_pk, owner_ccb=owner_ccb)
        return {"course_pk": parent_pk, "batch_id": batch_id,
                "registered": registered}

    async def stop_batch(self, batch_id: str) -> int:
        """停止整批：发停止信号并取消所有排队/运行任务。返回处理的视频数。"""
        video_ids = list(self._batch_videos.get(batch_id, set()))
        for vid in video_ids:
            stop = self._controls.get(vid)
            if stop:
                stop.set()
            task = self._tasks.get(vid)
            if task and not task.done():
                task.cancel()
        # 给事件循环一点时间处理取消
        await asyncio.sleep(0.5)

        # 修正仍标记 running 的库状态为 paused
        if video_ids:
            async with SessionLocal() as session:
                for vid in video_ids:
                    v = await session.get(Video, vid)
                    if v is not None and v.status not in ("done", "failed"):
                        v.status = "paused"
                await session.commit()

        self._batch_videos.pop(batch_id, None)
        return len(video_ids)

    def find_batch_of_course(self, workshop_pk: int) -> str | None:
        """根据专题班 id 无法直接得到 batch；由调用方在启动时自行留存。

        这里返回当前包含该专题班视频的批次（若只有一个批次在跑）。
        """
        # 简化：返回最近一个仍有成员的批次（workshop 页停止时使用）
        live = [b for b, vs in self._batch_videos.items() if vs]
        return live[-1] if live else None

    # ---------- worker ----------
    async def _guarded_worker(self, video_pk: int, stop: asyncio.Event) -> None:
        async with self._sem:
            # 若在拿到信号量前批次已被取消/停止，则直接退出（关键：不补位）
            if stop.is_set():
                await self._set_status_if_running(video_pk, "paused")
                await self._cleanup_ref(video_pk)
                return
            try:
                await self._set_status(video_pk, "running")
                await self._work_with_relogin(video_pk, stop)
            except asyncio.CancelledError:
                await self._set_status_if_running(video_pk, "paused")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("video %s failed", video_pk)
                await self._set_status(video_pk, "failed", error=str(exc)[:500])
            finally:
                await self._cleanup_ref(video_pk)

    async def _cleanup_ref(self, video_pk: int) -> None:
        batch = self._video_batch.pop(video_pk, None)
        if batch is not None:
            s = self._batch_videos.get(batch)
            if s is not None:
                s.discard(video_pk)
                if not s:
                    self._batch_videos.pop(batch, None)
        self._controls.pop(video_pk, None)
        self._tasks.pop(video_pk, None)

    async def _work_with_relogin(self, video_pk: int, stop: asyncio.Event) -> None:
        # 凭证不在服务端保存，401（票据失效）直接上抛，任务置 failed，前端引导重新登录。
        await self._run_one(video_pk, stop)

    async def _run_one(self, video_pk: int, stop: asyncio.Event) -> None:
        async with SessionLocal() as session:
            video = await session.get(Video, video_pk)
            if video is None:
                return
            knowledge_id = video.knowledge_id
            package_id = video.package_id
            user_knowledge_id = video.user_knowledge_id
            master_id = video.master_id
            parent_id = video.parent_id
            vtitle = video.title
            duration = video.duration
            start_seconds = video.learned_seconds if video.status != "done" else 0

        async def is_running() -> bool:
            return not stop.is_set()

        async def on_progress(view: float, done: bool | None, msg: str) -> None:
            async with SessionLocal() as s:
                v = await s.get(Video, video_pk)
                if v is None:
                    return
                v.learned_seconds = view
                if done is True:
                    v.status = "done"
                    v.completed_at = datetime.utcnow()
                    v.error = ""
                elif done is None:
                    v.status = "paused"
                else:
                    v.status = "running" if not stop.is_set() else "paused"
                s.add(
                    TaskLog(
                        video_id=video_pk,
                        view_schedule=view,
                        times=0,
                        result="ok" if done is not None else "pause",
                        message=msg[:255],
                    )
                )
                await s.commit()

        owner = self._video_owner.get(video_pk)

        async def client_factory():
            if owner is None:
                raise login_svc.LoginError("任务缺少归属用户")
            return await self.client_for_user(owner)

        await run_video(
            client_factory=client_factory,
            knowledge_id=knowledge_id,
            package_id=package_id,
            user_knowledge_id=user_knowledge_id,
            master_id=master_id,
            parent_id=parent_id,
            title=vtitle,
            duration=duration,
            start_seconds=start_seconds,
            speed=self._speed,
            on_progress=on_progress,
            is_running=is_running,
        )

    async def _set_status(self, video_pk: int, status: str, error: str = "") -> None:
        async with SessionLocal() as session:
            v = await session.get(Video, video_pk)
            if v is not None:
                v.status = status
                if error:
                    v.error = error
                await session.commit()

    async def _set_status_if_running(
        self, video_pk: int, status: str
    ) -> None:
        async with SessionLocal() as session:
            v = await session.get(Video, video_pk)
            if v is not None and v.status not in ("done", "failed"):
                v.status = status
                await session.commit()


runner = Runner()
